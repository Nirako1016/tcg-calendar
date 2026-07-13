"""
Deepseek LLM 解析模块
将自然语言消息解析为结构化 TCG 赛事数据
"""
import json
import os
import re
import traceback
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
4. 如果信息不完整，在对应字段填 null

请严格按照以下JSON格式返回，不要有任何其他文字：
{"tcg_type":"","event_name":"","start_date":"","end_date":"","city":""}"""


def parse_event_message(message: str) -> tuple[dict | None, str | None]:
    """
    用 Deepseek 解析自然语言消息，返回 (结构化事件数据, 错误信息)。
    成功时 error 为 None，失败时 data 为 None。
    """
    if not DEEPSEEK_API_KEY:
        return None, "未配置 DEEPSEEK_API_KEY 环境变量"

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
    }

    try:
        resp = requests.post(DEEPSEEK_API_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            error_body = resp.text[:300]
            return None, f"Deepseek API 返回 {resp.status_code}: {error_body}"

        resp_data = resp.json()
        content = resp_data["choices"][0]["message"]["content"]

        # 清理可能的 markdown 代码块标记
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)

        # 尝试从返回内容中提取 JSON（兼容非 json_object 模式的返回）
        json_match = re.search(r'\{[^{}]*\}', content)
        if not json_match:
            return None, f"Deepseek 返回内容不含 JSON: {content[:200]}"

        data = json.loads(json_match.group(0))

        # 校验必填字段
        if not data.get("event_name"):
            return None, f"Deepseek 未提取到赛事名称，返回: {data}"
        if not data.get("start_date"):
            return None, f"Deepseek 未提取到日期，返回: {data}"

        # 确保 end_date 有值
        if not data.get("end_date"):
            data["end_date"] = data["start_date"]

        # 校验日期格式
        for key in ("start_date", "end_date"):
            try:
                datetime.strptime(data[key], "%Y-%m-%d")
            except (ValueError, TypeError):
                return None, f"日期格式无效: {key}={data.get(key)}, 返回: {data}"

        # 校验开始日期不晚于结束日期
        if data["start_date"] > data["end_date"]:
            return None, f"开始日期晚于结束日期: {data['start_date']} > {data['end_date']}"

        return data, None

    except requests.Timeout:
        return None, "Deepseek API 请求超时"
    except requests.ConnectionError:
        return None, "无法连接 Deepseek API，请检查网络"
    except requests.RequestException as e:
        return None, f"Deepseek 请求异常: {e}"
    except (json.JSONDecodeError, KeyError) as e:
        return None, f"Deepseek 返回解析失败: {e}\n原始内容: {content[:200]}"
    except Exception as e:
        traceback.print_exc()
        return None, f"未知错误: {e}"


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
