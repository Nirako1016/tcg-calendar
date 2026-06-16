"""
Excel → ICS 日历订阅工具
FastAPI 后端服务
"""
import os
import io
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import openpyxl
from ics import Calendar, Event

# GitHub 推送模块
from github_push import push_ics_to_github, ensure_pages_enabled

# 内存缓存：存储最近解析的事件
import uuid
event_cache: dict[str, list[dict]] = {}

app = FastAPI(title="Excel to ICS Calendar Tool")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件目录
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
ics_output_dir = Path(__file__).parent / "output"
ics_output_dir.mkdir(exist_ok=True)

# GitHub 配置（通过环境变量读取）
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")


def parse_date_from_excel(value) -> str:
    """将 Excel 中的日期值解析为 YYYY-MM-DD 字符串"""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, str):
        # 尝试多种格式
        for fmt in ["%Y.%m.%d", "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"]:
            try:
                return datetime.strptime(value.strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        # 如果是中文格式如 2026年7月10日
        try:
            import re
            match = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日', value.strip())
            if match:
                return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        except Exception:
            pass
    raise ValueError(f"无法解析日期: {value}")


def parse_excel(file_bytes: bytes) -> list[dict]:
    """解析 Excel 文件，返回事件列表"""
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes))
    ws = wb.active
    events = []

    for row_idx, row in enumerate(ws.iter_rows(min_row=1, values_only=True), start=1):
        if row_idx == 1:
            # 跳过标题行
            continue

        # A:TCG品类, B:赛事名称, C:开始日期, D:结束日期, E:城市
        tcg_type = str(row[0]).strip() if row[0] else ""
        event_name = str(row[1]).strip() if row[1] else ""
        start_date_raw = row[2] if len(row) > 2 else None
        end_date_raw = row[3] if len(row) > 3 else None
        city = str(row[4]).strip() if len(row) > 4 and row[4] else ""

        # 跳过空行
        if not tcg_type or not event_name:
            continue
        if not start_date_raw:
            continue

        try:
            start_date = parse_date_from_excel(start_date_raw)
            end_date = parse_date_from_excel(end_date_raw) if end_date_raw else start_date
        except ValueError as e:
            print(f"第 {row_idx} 行日期解析失败: {e}")
            continue

        events.append({
            "row": row_idx,
            "tcg_type": tcg_type,
            "event_name": event_name,
            "start_date": start_date,
            "end_date": end_date,
            "city": city,
        })

    wb.close()
    return events


def generate_ics(events: list[dict], calendar_name: str = "TCG赛事日历") -> Calendar:
    """根据事件列表生成 ICS 日历"""
    cal = Calendar()
    cal.creator = "Excel to ICS Calendar Tool"

    for evt in events:
        e = Event()
        # 事件标题：【TCG品类】【赛事名称】
        e.name = f"【{evt['tcg_type']}】【{evt['event_name']}】"

        # 日期：跨天事件，结束日期 +1 天（ICS 中 DTEND 是排他性的）
        start_dt = datetime.strptime(evt["start_date"], "%Y-%m-%d")
        end_dt = datetime.strptime(evt["end_date"], "%Y-%m-%d") + timedelta(days=1)

        e.begin = start_dt.strftime("%Y-%m-%d")
        e.end = end_dt.strftime("%Y-%m-%d")
        e.make_all_day()

        # 地点
        if evt["city"]:
            e.location = evt["city"]

        # 描述
        desc_parts = [f"品类: {evt['tcg_type']}", f"赛事: {evt['event_name']}"]
        if evt["city"]:
            desc_parts.append(f"城市: {evt['city']}")
        e.description = "\n".join(desc_parts)

        cal.events.add(e)

    return cal


@app.get("/", response_class=HTMLResponse)
async def index():
    """返回前端页面"""
    html_path = static_dir / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return HTMLResponse("<h1>Please create static/index.html</h1>")


@app.post("/api/upload")
async def upload_excel(file: UploadFile = File(...)):
    """上传 Excel 并返回解析后的事件列表"""
    if not file.filename:
        raise HTTPException(400, "请选择文件")

    ext = Path(file.filename).suffix.lower()
    if ext not in [".xlsx", ".xls", ".xlsm"]:
        raise HTTPException(400, "请上传 Excel 文件（.xlsx / .xls）")

    try:
        content = await file.read()
        events = parse_excel(content)
        # 存缓存，返回 session_id
        session_id = str(uuid.uuid4())[:8]
        event_cache[session_id] = events
        return {"success": True, "count": len(events), "events": events, "session_id": session_id}
    except Exception as e:
        raise HTTPException(500, f"解析失败: {str(e)}")


@app.post("/api/generate")
async def generate_calendar(session_id: str = Form(...)):
    """根据已上传的 session_id 生成 ICS 文件"""
    events = event_cache.get(session_id)
    if not events:
        raise HTTPException(400, "会话已过期，请重新上传 Excel")

    try:
        cal = generate_ics(events)

        # 保存到 output 目录
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"tcg_calendar_{timestamp}.ics"
        output_path = ics_output_dir / filename
        output_path.write_text(cal.serialize(), encoding="utf-8")

        return {
            "success": True,
            "count": len(events),
            "events": events,
            "ics_filename": filename,
            "ics_content": cal.serialize(),
        }
    except Exception as e:
        raise HTTPException(500, f"生成失败: {str(e)}")


@app.get("/api/download/{filename}")
async def download_ics(filename: str):
    """下载生成的 ICS 文件"""
    filepath = ics_output_dir / filename
    if not filepath.exists():
        raise HTTPException(404, "文件不存在")

    return FileResponse(
        path=str(filepath),
        filename=filename,
        media_type="text/calendar",
    )


@app.get("/calendar.ics")
async def serve_latest_calendar():
    """返回最新的 ICS 日历（供外部订阅）"""
    files = sorted(ics_output_dir.glob("tcg_calendar_*.ics"), reverse=True)
    if not files:
        raise HTTPException(404, "暂无日历数据，请先上传 Excel 生成")

    content = files[0].read_text(encoding="utf-8")
    return PlainTextResponse(
        content=content,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": "inline; filename=calendar.ics",
        },
    )


@app.post("/api/publish")
async def publish_to_github(ics_content: str = Form(...)):
    """将 ICS 内容推送到 GitHub Pages"""
    result = push_ics_to_github(ics_content)
    return result


@app.get("/api/github/status")
async def github_status():
    """检查 GitHub 配置和 Pages 状态"""
    token_ok = bool(GITHUB_TOKEN)
    repo_ok = bool(GITHUB_REPO)

    status = {
        "token_configured": token_ok,
        "repo_configured": repo_ok,
        "repo": GITHUB_REPO if repo_ok else "",
    }

    if token_ok and repo_ok:
        pages = ensure_pages_enabled()
        status["pages"] = pages

    return status


# ========== 启动入口 ==========
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
