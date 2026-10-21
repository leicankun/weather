"""行程天气提醒 Skill 核心逻辑（scripts）。

从自然语言解析日期 → 取行程 → 查天气 → 拼装提醒指令。
依赖：
- app.services.itinerary_service（行程存取）
- app.services.weather_service（天气）
"""

from __future__ import annotations

from app.services import itinerary_service
from app.services.city_dict import lookup_city
from app.services.temporal_context import (
    DateResolutionError,
    date_header,
    date_metadata,
    iso_date,
    resolve_date,
    shanghai_today,
)
from app.services.weather_service import fetch_weather

# 行程未写地点 / 地点无法解析城市时的默认城市
DEFAULT_CITY = "广州"


def _resolve_city(location: str | None) -> str | None:
    """从行程地点文本解析城市规范名（如「上海迪士尼」→「上海」）。

    解析不到时返回 None，由调用方回落到默认城市。
    """
    if not location:
        return None
    info = lookup_city(location)
    return info.name if info else None


async def _weather_line(city: str, date_str: str) -> str:
    """查询指定城市在目标日期的天气文案（含预报告警兜底）。"""
    try:
        bundle = await fetch_weather(city)
    except Exception as exc:  # noqa: BLE001
        return f"{city}：天气查询失败（{exc}）"

    for d in bundle.daily:
        if d.date == date_str:
            text = f"{city}：{d.weather_desc}，气温 {d.temp_min}~{d.temp_max}°C"
            if d.precipitation_sum:
                text += f"，降水 {d.precipitation_sum}mm"
            return text
    return f"{city}：暂无该日精确预报，可参考近期天气趋势"


def parse_date_expression(text: str) -> str | None:
    """Compatibility parser; run() validates unsupported/ambiguous dates separately."""
    try:
        target, _ = resolve_date(text)
    except DateResolutionError:
        return None
    return target.isoformat() if target else None


async def run(user_id: int, query: str, target_date: str | None = None) -> str:
    """行程天气提醒主入口。"""
    if target_date:
        date_str = iso_date(target_date)
    else:
        target, _ = resolve_date(query)
        date_str = target.isoformat() if target else None

    if date_str is None:
        items = await itinerary_service.get_itinerary_by_date(user_id)
        if not items:
            return "你还没有安排行程。可先添加行程，我会结合天气给你出行提醒。"
        today = shanghai_today().isoformat()
        future = [it for it in items if it["date"] >= today]
        target = future or items
        date_str = target[0]["date"]
        items = [it for it in target if it["date"] == date_str]
        header = f"你最近的行程是 {date_str}："
    else:
        items = await itinerary_service.get_itinerary_by_date(user_id, date_str)
        if not items:
            return f"你在 {date_str} 没有安排行程。"
        header = f"你在 {date_str} 的行程安排："

    # 按行程地点解析城市（支持跨城市行程），逐城市查询当日天气
    cities: list[str] = []
    for it in items:
        city = _resolve_city(it.get("location"))
        if city and city not in cities:
            cities.append(city)
    if not cities:
        # 行程未写地点或地点无法识别城市时，回落到默认城市
        cities = [DEFAULT_CITY]

    weather_text = "\n".join([await _weather_line(c, date_str) for c in cities])

    weather_available = any(
        "暂无" not in line and "失败" not in line for line in weather_text.splitlines()
    )
    lines = [date_header(date_metadata(date_str, date_str if weather_available else None)), header]
    for it in items:
        line = f"- {it['start_time'] or '全天'} {it['title']}"
        if it["location"]:
            line += f" @{it['location']}"
        if it["activity"]:
            line += f"（{it['activity']}）"
        lines.append(line)

    lines.append(f"\n当天天气：\n{weather_text}")
    lines.append(
        "\n请基于以上行程和天气，逐条给出出行提醒与推荐（如雨天提醒带伞、高温提醒防晒补水、户外活动是否建议改期等）。"
    )
    return "\n".join(lines)
