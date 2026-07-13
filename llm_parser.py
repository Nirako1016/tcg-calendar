"""
Deepseek LLM 解析模块
将自然语言消息解析为结构化 TCG 赛事数据
"""
import json
import os
import re
from datetime import datetime

import requests

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

SYSTEM_PROMPT = """你是一个TCG赛事信息提取助手。用户会发送一段关于TCG赛事的自然语言描述，你需要提取以下字段：

- tcg_type: TCG品类（如：宝可梦卡牌、游戏王OCG、万智牌、数码宝贝卡牌、航海王卡牌等）
- event_name: 赛事名称
- start_date: 开始日期，格式 YYYY-MM-DD
- end_date: 结束日期，格式 YYYY-MM-DD（如果只提到一个日期，则与开始日期相同）
- city: 举办城市

注意事项：
1. 如果用户没有明确指定年份，默认使用当前年份
2. 如果用户说"7月15号到16号"，开始=07-15，结束=07-16
3. 如果用户说"7月15号"，开始和结束都是07-15
4. 如果信息不完整（比如缺少赛事名称），在对应字段填 null
5. 只返回纯 JSON，不要有任何其他文字、不要 markdown 代码块

返回格式：
{"tcg_type": "", "event_name": "", "start_date": "", "end_date": "", "city": ""}"""


def parse_event_message(message: str) -> dict | None:
    """
    用 Deepseek 解析自然语言消息，返回结构化事件数据。
    返回 None 表示解析失败。
    """
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY 环境变量")

    current_year = datetime.now().year
    user_msg = f"当前年份是{current_year}年。请解析以下消息：\n\n{message}"

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.1,
        "max_tokens": 300,
        "response_format": {"type": "json_object"},
    }

    try:
        resp = requests.post(DEEPSEEK_API_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        # 清理可能的 markdown 代码块标记
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)

        data = json.loads(content)

        # 校验必填字段
        if not data.get("event_name") or not data.get("start_date"):
            return None

        # 确保 end_date 有值
        if not data.get("end_date"):
            data["end_date"] = data["start_date"]

        # 校验日期格式
        for key in ("start_date", "end_date"):
            try:
                datetime.strptime(data[key], "%Y-%m-%d")
            except (ValueError, TypeError):
                return None

        # 校验开始日期不晚于结束日期
        if data["start_date"] > data["end_date"]:
            return None

        return data

    except (requests.RequestException, json.JSONDecodeError, KeyError) as e:
        print(f"[LLM解析失败] {e}")
        return None


def is_add_event_intent(message: str) -> bool:
    """
    判断消息是否是添加赛事的意图（而非命令）
    命令包括：列表、查看、删除、帮助等
    """
    msg = message.strip().lower()
    commands = ["列表", "查看", "列出", "删除", "移除", "帮助", "help", "清空", "同步", "status"]
    for cmd in commands:
        if msg.startswith(cmd):
            return False
    return True
