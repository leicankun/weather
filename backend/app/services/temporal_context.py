"""Shared Shanghai date resolution and tool-call constraints.

Only deterministic, supported expressions are resolved. Ambiguous/unsupported
dates must be clarified, never silently substituted with today's weather.
The serializable context lives in graph state so checkpoint resume keeps its anchor.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

CN = ZoneInfo("Asia/Shanghai")
DATE_TOOLS = {
    "get_weather",
    "get_forecast",
    "recommend_outfit",
    "plan_travel_route",
    "plan_trip",
    "check_itinerary_weather",
}
_PATTERN = re.compile(
    r"(?<!\d)\d{4}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}\s*[日号]?(?!\d)"
    r"|(?<!\d)\d{1,2}\s*月\s*\d{1,2}\s*[日号]?(?!\d)"
    r"|大后天|后天|明天|明日|今天|今日|昨天|昨日"
    r"|(?:\d+|[一二两三四五六七八九十]+)天[后前]"
    r"|(?:(?:下下|下|本|这|上上|上)(?:个)?(?:周|星期)|周|星期)(?:末|[一二三四五六日天])?"
)
_VAGUE = re.compile(
    r"月底|月初|下个月|下月|上个月|下半年|明年|后年|某天|择日|过几天|几天后|下旬|中旬|上旬"
)
_UNSUPPORTED_DATE = re.compile(
    r"\d+[-/年]\d+[-/月]\d+|\d{1,2}[-/]\d{1,2}|\d{1,2}[日号]|礼拜|[一二三四五六七八九十]+月"
)
_ANAPHORA = re.compile(r"那|当天|当日|同一天|刚才|之前说|这个行程|这趟|那趟")


class DateResolutionError(ValueError):
    """A user date cannot safely become a single calendar date."""


def shanghai_today() -> date:
    return datetime.now(CN).date()


def iso_date(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
        raise DateResolutionError("请提供有效的 YYYY-MM-DD 日期。")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise DateResolutionError("日期不存在，请确认年月日。") from exc


def _active_text(text: str) -> str:
    # Last explicit correction supersedes the original date, including a later
    # negated old date: 改成2026-10-31，不是明天.
    parts = re.split(r"改(?:为|成|到)|改期到|调整(?:为|到)|换成", text)
    return parts[-1]


def _number(text: str) -> int:
    if text.isdigit():
        return int(text)
    digits = {c: n for n, c in enumerate("零一二三四五六七八九")}
    digits["两"] = 2
    if "十" in text:
        left, right = text.split("十", 1)
        return digits.get(left, 1) * 10 + digits.get(right, 0)
    return digits.get(text, 0)


def _resolve_token(token: str, ref: date) -> date:
    token = re.sub(r"\s+", "", token)
    full = re.fullmatch(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})[日号]?", token)
    if full:
        return date(*map(int, full.groups()))
    short = re.fullmatch(r"(\d{1,2})月(\d{1,2})[日号]?", token)
    if short:
        month, day = map(int, short.groups())
        for year in (ref.year, ref.year + 1):
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue
            if candidate >= ref:
                return candidate
        raise ValueError("invalid month/day")
    offsets = {
        "大后天": 3,
        "后天": 2,
        "明天": 1,
        "明日": 1,
        "今天": 0,
        "今日": 0,
        "昨天": -1,
        "昨日": -1,
    }
    if token in offsets:
        return ref + timedelta(days=offsets[token])
    relative = re.fullmatch(r"(.+)天([后前])", token)
    if relative:
        offset = _number(relative[1]) * (1 if relative[2] == "后" else -1)
        return ref + timedelta(days=offset)
    week = re.fullmatch(
        r"(下下|下|本|这|上上|上)?(?:个)?(?:周|星期)(末|[一二三四五六日天])?", token
    )
    if week:
        prefix, weekday = week.groups()
        index = 5 if weekday == "末" else "一二三四五六日天".find(weekday or "一")
        index = min(index, 6)
        monday = ref - timedelta(days=ref.weekday())
        weeks = {"下下": 2, "下": 1, "本": 0, "这": 0, "上": -1, "上上": -2}
        target = monday + timedelta(days=weeks.get(prefix, 0) * 7 + index)
        if prefix is None and target < ref:
            target += timedelta(days=7)
        return target
    raise ValueError("unsupported date")


def date_mentions(text: str, ref: date) -> tuple[list[dict], bool]:
    """Return non-negated mentions with spans; preserve invalid date signals."""
    text = _active_text(text or "")
    mentions = []
    unresolved = bool(_VAGUE.search(text))
    for match in _PATTERN.finditer(text):
        if re.search(
            r"(?:不是|而非|不要把|不要用|不要拿|不能用|不可用|不能拿|别用|不要|不选|不去|别选)\s*$",
            text[: match.start()],
        ):
            continue
        try:
            target = _resolve_token(match[0], ref)
        except (ValueError, OverflowError):
            unresolved = True
            continue
        mentions.append(
            {
                "expression": match[0],
                "date": target.isoformat(),
                "start": match.start(),
                "end": match.end(),
            }
        )
    remainder = text
    for match in reversed(list(_PATTERN.finditer(text))):
        remainder = remainder[: match.start()] + " " + remainder[match.end() :]
    unresolved |= bool(_UNSUPPORTED_DATE.search(remainder))
    if not mentions and not unresolved:
        # Keep the existing lunar/public holiday calendar as the sole authority.
        from app.services import holiday_calendar

        resolved = holiday_calendar.resolve(text, ref)
        if resolved:
            festival, target = resolved
            start = text.find(festival.name)
            mentions.append(
                {
                    "expression": festival.name,
                    "date": target.isoformat(),
                    "start": max(0, start),
                    "end": max(0, start) + len(festival.name),
                }
            )
        elif holiday_calendar.find_festival(text):
            unresolved = True
    return mentions, unresolved


def resolve_date(text: str, ref: date | None = None) -> tuple[date | None, str | None]:
    mentions, unresolved = date_mentions(text, ref or shanghai_today())
    dates = {item["date"] for item in mentions}
    if unresolved or len(dates) > 1:
        raise DateResolutionError("日期不明确，请指定一个 YYYY-MM-DD 日期，多个日期请分别说明。")
    if not mentions:
        return None, None
    return date.fromisoformat(mentions[0]["date"]), mentions[0]["expression"]


def build_temporal_context(
    question: str, history: list[dict] | None = None, ref: date | None = None
) -> dict:
    ref = ref or shanghai_today()
    text = _active_text(question)
    mentions, unresolved = date_mentions(question, ref)
    source = "current_turn"
    if not mentions and not unresolved and _ANAPHORA.search(question):
        for item in reversed(history or []):
            # Assistant statements cannot override the user's own dates.
            if item.get("role") != "user":
                continue
            # Relative history dates belong to the day that user message was
            # written, not the day on which the follow-up is received.
            history_ref = ref
            if item.get("date_reference"):
                try:
                    history_ref = date.fromisoformat(item["date_reference"])
                except ValueError:
                    unresolved = True
                    break
            found, invalid = date_mentions(item.get("content", ""), history_ref)
            if invalid:
                unresolved = True
                break
            if found:
                mentions = found
                text = _active_text(item["content"])
                source = "history_user"
                break
        if not mentions and any(item.get("role") == "summary" for item in history or []):
            unresolved = True  # A lossy summary alone cannot establish a date anchor.
    dates = list(dict.fromkeys(item["date"] for item in mentions))
    tasks = []
    keywords = {
        "recommend_outfit": ("穿", "搭配", "带伞", "雨具"),
        "plan_travel_route": ("路线", "怎么走", "怎么去", "从", "交通"),
        "plan_trip": ("安排", "规划", "行程", "日游", "玩"),
        "check_itinerary_weather": ("已有", "保存", "我的行程", "什么安排", "查"),
        "get_weather": ("天气", "气温", "下雨", "温度"),
        "get_forecast": ("天气", "气温", "下雨", "温度"),
    }
    for i, item in enumerate(mentions):
        # Include a task written before its date: 穿搭看明天，路线看后天.
        prefix_start = max(text.rfind(mark, 0, item["start"]) for mark in "，,。；;\n") + 1
        start = max(prefix_start, mentions[i - 1]["end"] if i else 0)
        end = mentions[i + 1]["start"] if i + 1 < len(mentions) else len(text)
        if i + 1 < len(mentions):
            boundary = max(text.rfind(mark, item["end"], end) for mark in "，,。；;\n")
            if boundary >= 0 and any(
                word in text[boundary:end] for words in keywords.values() for word in words
            ):
                end = boundary
        segment = text[start:end]
        tasks.append(
            {
                "date": item["date"],
                "expression": item["expression"],
                "text": segment,
                "tools": [
                    name
                    for name, words in keywords.items()
                    if any(word in segment for word in words)
                ],
            }
        )
    return {
        "current_date": ref.isoformat(),
        "timezone": "Asia/Shanghai",
        "source": source,
        "dates": dates,
        "tasks": tasks,
        "unresolved": unresolved,
    }


def temporal_prompt(context: dict) -> str:
    return (
        "\n【本轮日期约束（服务端计算）】\n"
        + json.dumps(context, ensure_ascii=False)
        + "\n按 tasks 将日期绑定到各自任务。调用日期相关工具必须传 target_date（YYYY-MM-DD）；"
        "同一工具涉及多天时分别调用。无明确日期才可使用默认日期。"
        "unresolved=true 时先请用户澄清日期。工具拒绝日期时请修正参数，不能改查今天。"
        "回答写明具体日期；按 weather_date 使用天气，缺失时说明无法确认，不得套用其他日。"
    )


def guard_tool_date(name: str, args: dict, context: dict) -> dict:
    """Validate before business IO; fill only a uniquely bound missing date."""
    args = dict(args)
    if name not in DATE_TOOLS:
        return args
    if context.get("unresolved"):
        raise DateResolutionError("用户日期尚未明确，请先澄清，不能默认查询今天。")
    candidates = list(
        dict.fromkeys(task["date"] for task in context.get("tasks", []) if name in task["tools"])
    )
    if name in {"get_weather", "get_forecast"} or any(
        word in "".join(task["text"] for task in context.get("tasks", []))
        for word in ("对比", "比较", "分别")
    ):
        candidates = []
    allowed = candidates or context.get("dates", [])
    target = args.get("target_date")
    if target:
        target = iso_date(target)
        if not allowed and target != context.get("current_date"):
            raise DateResolutionError("用户未指定该日期，请先确认，不能自行选择未来或过去日期。")
        if allowed and target not in allowed:
            raise DateResolutionError(f"工具日期 {target} 与该任务不符，应使用 {allowed}。")
    elif len(allowed) == 1:
        target = allowed[0]
    elif len(allowed) > 1:
        raise DateResolutionError(
            f"该请求涉及多个日期 {allowed}，请为本次工具调用明确 target_date。"
        )
    elif name in {"recommend_outfit", "plan_travel_route", "plan_trip"}:
        target = context["current_date"]
    if target:
        args["target_date"] = target
    return args


def date_metadata(target: str, weather_date: str | None = None) -> dict:
    return {
        "requested_date": target,
        "effective_date": target,
        "weather_date": weather_date,
        "weather_status": "available" if weather_date else "unavailable",
    }


def date_header(metadata: dict) -> str:
    return "【日期核对】" + json.dumps(metadata, ensure_ascii=False)


def answer_date_issues(context: dict, text: str) -> list[str]:
    """Deterministic rejection of explicit relative/calendar date contradictions.

    This is deliberately narrow: it does not claim to judge all temporal meaning.
    Weather values and natural-language attribution still need evidence review.
    """
    if not context.get("current_date"):
        return []
    ref = date.fromisoformat(context["current_date"])
    relative = r"大后天|后天|明天|明日|今天|今日|昨天|昨日"
    absolute = r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日号]?"
    forward = re.compile(rf"(?P<relative>{relative})[\s*（(：:，,是即为]*(?P<absolute>{absolute})")
    backward = re.compile(rf"(?P<absolute>{absolute})[\s*（(：:，,是即为]*(?P<relative>{relative})")
    issues = []
    for pattern in (forward, backward):
        for match in pattern.finditer(text):
            try:
                expected = _resolve_token(match["relative"], ref).isoformat()
                actual = _resolve_token(match["absolute"], ref).isoformat()
            except (ValueError, OverflowError):
                issues.append("回答包含不存在的日期。")
                continue
            if expected != actual:
                issues.append(f"{match['relative']}应为{expected}，回答却标为{actual}。")
    return list(dict.fromkeys(issues))
