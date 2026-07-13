"""
TCG 赛事消息解析模块（规则匹配，零依赖）
从自然语言消息中提取赛事信息
"""
import re
from datetime import datetime


def _parse_date(date_str: str, default_year: int) -> str | None:
    """将中文日期字符串解析为 YYYY-MM-DD"""
    date_str = date_str.strip()

    # 2026年7月15日 / 2026-07-15 / 2026.7.15 / 2026/7/15
    m = re.match(r'(\d{4})\s*[年.\-/]\s*(\d{1,2})\s*[月.\-/]\s*(\d{1,2})\s*日?$', date_str)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # 7月15日 / 7.15 / 7/15
    m = re.match(r'(\d{1,2})\s*[月.\-/]\s*(\d{1,2})\s*日?$', date_str)
    if m:
        return f"{default_year:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    # 7月15号 / 15号
    m = re.match(r'(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]$', date_str)
    if m:
        return f"{default_year:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    # 7.15 (纯数字日期)
    m = re.match(r'(\d{1,2})\.(\d{1,2})$', date_str)
    if m:
        return f"{default_year:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    # 0715 (4位数字)
    m = re.match(r'(\d{2})(\d{2})$', date_str)
    if m:
        return f"{default_year:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    return None


def parse_event_message(message: str) -> tuple[dict | None, str | None]:
    """
    用规则匹配解析自然语言消息，返回 (结构化事件数据, 错误信息)。
    成功时 error 为 None，失败时 data 为 None。

    支持的消息格式（按优先级）：
    1. TCG品类 赛事名称 开始日期 到 结束日期 城市
    2. TCG品类 赛事名称 日期 城市 （单天赛事）
    """
    msg = message.strip()
    current_year = datetime.now().year

    # ========== 模式1: 有明确日期范围的 ==========
    # 匹配 "到" / "至" / "~" / "-" 分隔的日期范围
    # 例: 宝可梦卡牌 上海公开赛 7月15日到16日 上海
    # 例: 游戏王 广州站 7.15~7.17 广州
    # 例: 万智牌 北京大奖赛 2026年7月15日-7月20日 北京

    date_range_patterns = [
        # "到" / "至" / "~" / "-" 分隔
        r'(.+?)\s+(.+?)\s+(\S{2,})\s*[到至~\-]\s*(\S{2,})\s+(.+)',
        # 中文日期连写: 7月15日到16日
        r'(.+?)\s+(.+?)\s+(\d{1,2}月\d{1,2}[号日])\s*[到至~\-]\s*(\d{1,2}月?\d{0,2}[号日]?)\s+(.+)',
    ]

    for pattern in date_range_patterns:
        m = re.match(pattern, msg)
        if m:
            tcg_type = m.group(1).strip()
            event_name = m.group(2).strip()
            start_raw = m.group(3).strip()
            end_raw = m.group(4).strip()
            city = m.group(5).strip()

            start_date = _parse_date(start_raw, current_year)
            end_date = _parse_date(end_raw, current_year)

            if start_date and end_date:
                # 如果 end_date 是 "16日" 这种简写，尝试补全
                if end_date and not end_date.startswith(str(current_year)):
                    # 已经是补全过的
                    pass

                return {
                    "tcg_type": tcg_type,
                    "event_name": event_name,
                    "start_date": start_date,
                    "end_date": end_date,
                    "city": city,
                }, None

            # 如果 end 是 "16日" 简写，尝试从 start 拼接
            if start_date:
                end_simple = re.match(r'(\d{1,2})\s*[月日号]?$', end_raw)
                if end_simple:
                    day = int(end_simple.group(1))
                    year_month = start_date[:8]  # YYYY-MM-
                    end_date = f"{year_month}{day:02d}"
                    return {
                        "tcg_type": tcg_type,
                        "event_name": event_name,
                        "start_date": start_date,
                        "end_date": end_date,
                        "city": city,
                    }, None

    # ========== 模式2: 单天赛事 ==========
    # 例: 宝可梦卡牌 上海公开赛 7月15日 上海
    m = re.match(r'(.+?)\s+(.+?)\s+(\S{3,})\s+(.+)', msg)
    if m:
        tcg_type = m.group(1).strip()
        event_name = m.group(2).strip()
        date_raw = m.group(3).strip()
        city = m.group(4).strip()

        date = _parse_date(date_raw, current_year)
        if date:
            return {
                "tcg_type": tcg_type,
                "event_name": event_name,
                "start_date": date,
                "end_date": date,
                "city": city,
            }, None

    # ========== 模式3: 无城市的日期范围 ==========
    m = re.match(r'(.+?)\s+(.+?)\s+(\S{2,})\s*[到至~\-]\s*(\S{2,})$', msg)
    if m:
        tcg_type = m.group(1).strip()
        event_name = m.group(2).strip()
        start_raw = m.group(3).strip()
        end_raw = m.group(4).strip()

        start_date = _parse_date(start_raw, current_year)
        end_date = _parse_date(end_raw, current_year)

        if start_date and end_date:
            return {
                "tcg_type": tcg_type,
                "event_name": event_name,
                "start_date": start_date,
                "end_date": end_date,
                "city": "",
            }, None

    # ========== 模式4: 无城市的单天 ==========
    m = re.match(r'(.+?)\s+(.+?)\s+(\S{3,})$', msg)
    if m:
        tcg_type = m.group(1).strip()
        event_name = m.group(2).strip()
        date_raw = m.group(3).strip()

        date = _parse_date(date_raw, current_year)
        if date:
            return {
                "tcg_type": tcg_type,
                "event_name": event_name,
                "start_date": date,
                "end_date": date,
                "city": "",
            }, None

    return None, (
        "无法从消息中提取完整的赛事信息。\n"
        "请按格式发送：品类 赛事名称 日期 城市\n"
        "例：宝可梦卡牌 上海公开赛 7月15日到16日 上海"
    )


def is_add_event_intent(message: str) -> bool:
    """
    判断消息是否是添加赛事的意图（而非命令）
    """
    msg = message.strip().lower()
    commands = ["列表", "查看", "列出", "删除", "移除", "帮助", "help", "清空", "同步", "status"]
    for cmd in commands:
        if msg.startswith(cmd):
            return False
    return True
