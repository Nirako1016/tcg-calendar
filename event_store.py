"""
事件持久化存储模块
使用 GitHub 仓库中的 events.json 作为持久化存储
（Render 免费版文件系统重启会丢失数据，所以存 GitHub）
"""
import base64
import json
import os
from datetime import datetime

import requests

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_API = "https://api.github.com"

EVENTS_FILE = "events.json"


def _headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }


def _get_file_url(filename: str):
    return f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{filename}"


def fetch_events() -> list[dict]:
    """
    从 GitHub 仓库读取 events.json，返回事件列表。
    如果文件不存在或读取失败，返回空列表。
    """
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return []

    try:
        resp = requests.get(
            _get_file_url(EVENTS_FILE),
            headers=_headers(),
            params={"ref": GITHUB_BRANCH},
            timeout=15,
        )
        if resp.status_code == 404:
            return []
        if resp.status_code != 200:
            print(f"[fetch_events] GitHub API 返回 {resp.status_code}")
            return []

        data = resp.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        events = json.loads(content)
        if not isinstance(events, list):
            return []
        return events

    except Exception as e:
        print(f"[fetch_events] 读取失败: {e}")
        return []


def save_events(events: list[dict]) -> dict:
    """
    将事件列表保存到 GitHub 仓库的 events.json。
    返回 {"success": bool, "message": str}
    """
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return {"success": False, "message": "GitHub 未配置"}

    content = json.dumps(events, ensure_ascii=False, indent=2)
    content_b64 = base64.b64encode(content.encode("utf-8")).decode("utf-8")

    # 获取当前文件的 SHA（如果存在）
    sha = None
    try:
        resp = requests.get(
            _get_file_url(EVENTS_FILE),
            headers=_headers(),
            params={"ref": GITHUB_BRANCH},
            timeout=15,
        )
        if resp.status_code == 200:
            sha = resp.json().get("sha")
    except Exception:
        pass

    payload = {
        "message": f"Update events.json - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "content": content_b64,
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    try:
        resp = requests.put(
            _get_file_url(EVENTS_FILE),
            json=payload,
            headers=_headers(),
            timeout=15,
        )
        if resp.status_code in [200, 201]:
            return {"success": True, "message": "保存成功"}
        else:
            err = resp.json().get("message", "Unknown")
            return {"success": False, "message": f"GitHub API 错误: {err}"}
    except Exception as e:
        return {"success": False, "message": f"保存失败: {e}"}


def add_event(event: dict) -> dict:
    """
    添加单个事件到 events.json。
    返回 {"success": bool, "total": int, "message": str}
    """
    events = fetch_events()

    # 去重：如果同品类+同赛事名+同日期已存在，则更新
    existing_idx = None
    for i, evt in enumerate(events):
        if (
            evt.get("tcg_type") == event["tcg_type"]
            and evt.get("event_name") == event["event_name"]
            and evt.get("start_date") == event["start_date"]
        ):
            existing_idx = i
            break

    if existing_idx is not None:
        events[existing_idx] = event
        action = "更新"
    else:
        events.append(event)
        action = "添加"

    result = save_events(events)
    return {
        "success": result["success"],
        "total": len(events),
        "action": action,
        "message": result["message"],
    }


def remove_events(keyword: str) -> dict:
    """
    删除赛事名称包含关键词的事件。
    返回 {"success": bool, "removed": int, "total": int, "message": str}
    """
    events = fetch_events()
    original_count = len(events)
    events = [e for e in events if keyword not in e.get("event_name", "")]
    removed = original_count - len(events)

    if removed == 0:
        return {"success": True, "removed": 0, "total": original_count, "message": "未找到匹配的赛事"}

    result = save_events(events)
    return {
        "success": result["success"],
        "removed": removed,
        "total": len(events),
        "message": result["message"],
    }


def clear_all_events() -> dict:
    """清空所有事件"""
    result = save_events([])
    return {"success": result["success"], "total": 0, "message": result["message"]}
