"""
GitHub Pages 推送模块
将生成的 ICS 文件推送到 GitHub 仓库，实现 Pages 托管
"""
import os
import base64
import requests
from pathlib import Path

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")  # 格式: username/repo
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_API = "https://api.github.com"


def push_ics_to_github(ics_content: str, filename: str = "calendar.ics") -> dict:
    """
    将 ICS 内容推送到 GitHub 仓库。
    返回 {"success": bool, "url": str, "message": str}
    """
    if not GITHUB_TOKEN:
        return {
            "success": False,
            "url": "",
            "message": "未配置 GITHUB_TOKEN 环境变量，请先设置 GitHub Personal Access Token"
        }
    if not GITHUB_REPO:
        return {
            "success": False,
            "url": "",
            "message": "未配置 GITHUB_REPO 环境变量，格式: username/repo"
        }

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    api_url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{filename}"

    # 先检查文件是否已存在（获取 SHA）
    try:
        resp = requests.get(api_url, headers=headers, timeout=15)
        sha = resp.json().get("sha") if resp.status_code == 200 else None
    except Exception:
        sha = None

    # 准备提交
    content_base64 = base64.b64encode(ics_content.encode("utf-8")).decode("utf-8")
    commit_message = f"Update calendar.ics - {ics_content[:50]}..."

    payload = {
        "message": commit_message,
        "content": content_base64,
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    try:
        resp = requests.put(api_url, json=payload, headers=headers, timeout=15)
        if resp.status_code in [200, 201]:
            # 提取 GitHub Pages URL
            owner = GITHUB_REPO.split("/")[0]
            repo_name = GITHUB_REPO.split("/")[1]
            pages_url = f"https://{owner}.github.io/{repo_name}/{filename}"
            return {
                "success": True,
                "url": pages_url,
                "webcal_url": pages_url.replace("https://", "webcal://"),
                "message": "推送成功",
            }
        else:
            return {
                "success": False,
                "url": "",
                "message": f"GitHub API 返回错误: {resp.status_code} - {resp.json().get('message', 'Unknown')}",
            }
    except Exception as e:
        return {
            "success": False,
            "url": "",
            "message": f"推送失败: {str(e)}",
        }


def ensure_pages_enabled():
    """检查 GitHub Pages 是否已启用"""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return {"enabled": False, "message": "GitHub 未配置"}

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        resp = requests.get(
            f"{GITHUB_API}/repos/{GITHUB_REPO}/pages",
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "enabled": True,
                "url": data.get("html_url", ""),
                "message": "Pages 已启用",
            }
        else:
            return {
                "enabled": False,
                "message": "GitHub Pages 未启用，请在仓库 Settings → Pages 中开启，Source 选择 main 分支",
            }
    except Exception as e:
        return {"enabled": False, "message": str(e)}
