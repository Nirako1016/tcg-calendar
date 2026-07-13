"""
Excel → ICS 日历订阅工具 + 企业微信机器人
FastAPI 后端服务
"""
import os
import io
import json
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import openpyxl
import requests as http_requests
from ics import Calendar, Event

# GitHub 推送模块
from github_push import push_ics_to_github, ensure_pages_enabled
# WeCom 加解密
from wecom_crypto import (
    verify_signature,
    decrypt_message,
    build_encrypted_reply,
    extract_message,
    parse_decrypted_xml,
    build_text_reply,
)
# LLM 解析
from llm_parser import parse_event_message, is_add_event_intent
# 事件持久化
from event_store import fetch_events, add_event, remove_events, clear_all_events

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

# 企业微信配置
WECOM_CORP_ID = os.environ.get("WECOM_CORP_ID", "")
WECOM_AGENT_ID = int(os.environ.get("WECOM_AGENT_ID", "0"))
WECOM_CORP_SECRET = os.environ.get("WECOM_CORP_SECRET", "")
WECOM_TOKEN = os.environ.get("WECOM_TOKEN", "")
WECOM_ENCODING_AES_KEY = os.environ.get("WECOM_ENCODING_AES_KEY", "")

# access_token 缓存
_wecom_access_token: str = ""
_wecom_token_expires: float = 0


def get_wecom_access_token() -> str:
    """获取企业微信 access_token（带缓存）"""
    global _wecom_access_token, _wecom_token_expires
    if _wecom_access_token and time.time() < _wecom_token_expires:
        return _wecom_access_token

    url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
    params = {"corpid": WECOM_CORP_ID, "corpsecret": WECOM_CORP_SECRET}
    resp = http_requests.get(url, params=params, timeout=10)
    data = resp.json()
    if data.get("errcode") == 0:
        _wecom_access_token = data["access_token"]
        _wecom_token_expires = time.time() + data.get("expires_in", 7200) - 300
        return _wecom_access_token
    raise RuntimeError(f"获取 access_token 失败: {data}")


def send_wecom_message(user_id: str, content: str):
    """主动发送消息给用户"""
    token = get_wecom_access_token()
    url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
    payload = {
        "touser": user_id,
        "msgtype": "text",
        "agentid": WECOM_AGENT_ID,
        "text": {"content": content},
    }
    resp = http_requests.post(url, json=payload, timeout=10)
    return resp.json()


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

        # 校验：开始日期不能晚于结束日期
        if start_date > end_date:
            raise ValueError(f"第 {row_idx} 行「{event_name}」开始日期（{start_date}）晚于结束日期（{end_date}），请检查表格")

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

        # 日期：直接使用表里填写的日期，不做任何偏移
        start_dt = datetime.strptime(evt["start_date"], "%Y-%m-%d")
        end_dt = datetime.strptime(evt["end_date"], "%Y-%m-%d")

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


# ========== 企业微信机器人回调 ==========

def sync_events_to_ics() -> dict:
    """从 GitHub 读取所有事件 → 生成 ICS → 推送到 GitHub Pages"""
    events = fetch_events()
    if not events:
        return {"success": False, "message": "暂无事件数据"}

    cal = generate_ics(events)
    ics_content = cal.serialize()
    result = push_ics_to_github(ics_content)
    return result


def handle_wecom_message(msg: dict) -> str:
    """
    处理解密后的企业微信消息，返回回复文本
    """
    msg_type = msg.get("MsgType", "")
    content = msg.get("Content", "").strip() if msg.get("Content") else ""
    from_user = msg.get("FromUserName", "")

    if msg_type != "text":
        return "目前只支持文字消息哦，直接把赛事信息发给我就行～"

    # 命令：帮助
    if content.lower() in ("帮助", "help", "?", "？"):
        return (
            "📋 TCG赛事日历机器人 使用说明\n\n"
            "1️⃣ 添加赛事：直接发送赛事信息，例如：\n"
            "   宝可梦卡牌 上海公开赛 7月15日到16日 上海\n\n"
            "2️⃣ 查看所有赛事：发送「列表」\n\n"
            "3️⃣ 删除赛事：发送「删除 关键词」（如：删除 上海公开赛）\n\n"
            "4️⃣ 同步日历：发送「同步」手动推送日历到订阅链接\n\n"
            "📅 订阅链接：\n"
            "https://nirako1016.github.io/tcg-calendar/calendar.ics"
        )

    # 命令：列表
    if content.lower() in ("列表", "查看", "列出"):
        events = fetch_events()
        if not events:
            return "📋 当前没有任何赛事记录\n\n发送赛事信息即可添加，例如：\n宝可梦卡牌 上海公开赛 7月15日到16日 上海"

        lines = [f"📋 共 {len(events)} 场赛事：\n"]
        # 按开始日期排序
        events_sorted = sorted(events, key=lambda e: e.get("start_date", ""))
        for i, evt in enumerate(events_sorted, 1):
            dates = evt["start_date"]
            if evt["end_date"] != evt["start_date"]:
                dates += f" ~ {evt['end_date']}"
            city = f" · {evt['city']}" if evt.get("city") else ""
            lines.append(f"{i}. 【{evt['tcg_type']}】{evt['event_name']}\n   {dates}{city}")
        return "\n".join(lines)

    # 命令：删除
    if content.lower().startswith("删除") or content.lower().startswith("移除"):
        keyword = content[2:].strip() or content[3:].strip()
        if not keyword:
            return "请指定要删除的赛事关键词，例如：删除 上海公开赛"
        result = remove_events(keyword)
        if result["success"] and result["removed"] > 0:
            sync_events_to_ics()
            return f"✅ 已删除 {result['removed']} 场匹配「{keyword}」的赛事\n当前剩余 {result['total']} 场赛事\n日历已自动更新"
        elif result["success"]:
            return f"未找到包含「{keyword}」的赛事"
        else:
            return f"删除失败：{result['message']}"

    # 命令：同步
    if content.lower() in ("同步", "sync", "status"):
        result = sync_events_to_ics()
        if result["success"]:
            return f"✅ 日历已同步到 GitHub Pages\n订阅链接：{result.get('url', '')}"
        else:
            return f"❌ 同步失败：{result.get('message', '未知错误')}"

    # 命令：清空
    if content.lower() in ("清空", "清除"):
        result = clear_all_events()
        if result["success"]:
            return "✅ 已清空所有赛事数据"
        else:
            return f"清空失败：{result['message']}"

    # 默认：尝试解析为赛事信息
    if not is_add_event_intent(content):
        return "无法识别的命令。发送「帮助」查看使用说明。"

    parsed = parse_event_message(content)
    if not parsed:
        return (
            "⚠️ 未能识别赛事信息，请确保包含品类、赛事名称和日期。\n\n"
            "示例：宝可梦卡牌 上海公开赛 7月15日到16日 上海\n\n"
            "发送「帮助」查看完整说明。"
        )

    # 添加事件
    event = {
        "tcg_type": parsed["tcg_type"],
        "event_name": parsed["event_name"],
        "start_date": parsed["start_date"],
        "end_date": parsed["end_date"],
        "city": parsed.get("city", ""),
    }
    add_result = add_event(event)

    if not add_result["success"]:
        return f"❌ 添加失败：{add_result['message']}"

    # 自动同步 ICS
    dates = event["start_date"]
    if event["end_date"] != event["start_date"]:
        dates += f" ~ {event['end_date']}"
    city_text = f" · {event['city']}" if event["city"] else ""

    sync_result = sync_events_to_ics()
    sync_text = "日历已自动更新" if sync_result.get("success") else f"日历同步失败：{sync_result.get('message', '')}"

    return (
        f"✅ 已{add_result['action']}赛事：\n"
        f"【{event['tcg_type']}】{event['event_name']}\n"
        f"📅 {dates}{city_text}\n\n"
        f"当前共 {add_result['total']} 场赛事\n"
        f"{sync_text}"
    )


@app.get("/wecom/callback")
async def wecom_verify(
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    """企业微信回调 URL 验证（GET）"""
    signature = verify_signature(WECOM_TOKEN, timestamp, nonce, echostr)
    if signature != msg_signature:
        raise HTTPException(403, "签名验证失败")

    decrypted = decrypt_message(WECOM_ENCODING_AES_KEY, echostr, WECOM_CORP_ID)
    return PlainTextResponse(content=decrypted, media_type="text/plain")


@app.post("/wecom/callback")
async def wecom_callback(
    request: Request,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
):
    """企业微信消息回调（POST）"""
    body = await request.body()
    xml_str = body.decode("utf-8")

    # 提取加密内容
    encrypt = extract_message(xml_str)
    if not encrypt:
        return PlainTextResponse("error", status_code=400)

    # 验证签名
    signature = verify_signature(WECOM_TOKEN, timestamp, nonce, encrypt)
    if signature != msg_signature:
        return PlainTextResponse("signature error", status_code=403)

    # 解密
    try:
        decrypted_xml = decrypt_message(WECOM_ENCODING_AES_KEY, encrypt, WECOM_CORP_ID)
    except Exception as e:
        print(f"[解密失败] {e}")
        return PlainTextResponse("decrypt error", status_code=500)

    # 解析消息
    msg = parse_decrypted_xml(decrypted_xml)
    print(f"[收到消息] From: {msg.get('FromUserName')}, Content: {msg.get('Content')}")

    # 处理消息
    try:
        reply_text = handle_wecom_message(msg)
    except Exception as e:
        print(f"[处理消息异常] {e}")
        reply_text = f"处理消息时出错：{e}"

    # 构建加密回复
    from_user = msg.get("FromUserName", "")
    to_user = msg.get("ToUserName", "")
    reply_xml = build_text_reply(from_user, to_user, reply_text)
    encrypted_reply = build_encrypted_reply(
        WECOM_TOKEN, WECOM_ENCODING_AES_KEY, WECOM_CORP_ID, reply_xml
    )

    return Response(content=encrypted_reply, media_type="application/xml")


@app.get("/api/bot/status")
async def bot_status():
    """检查企业微信机器人配置状态"""
    return {
        "wecom_configured": bool(WECOM_CORP_ID and WECOM_CORP_SECRET and WECOM_TOKEN and WECOM_ENCODING_AES_KEY),
        "deepseek_configured": bool(os.environ.get("DEEPSEEK_API_KEY")),
        "github_configured": bool(GITHUB_TOKEN and GITHUB_REPO),
        "events_count": len(fetch_events()),
    }


# ========== 启动入口 ==========
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
