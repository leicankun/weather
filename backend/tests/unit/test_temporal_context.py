"""Regression cases from the frozen 2026-10-21 date/tool evaluation."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import temporal_context as tc
from mcp_server import tools
from skills.itinerary_planner.scripts import planner
from skills.itinerary_reminder.scripts import reminder
from skills.outfit_recommend.scripts import outfit_engine
from skills.travel_planning.scripts import route_planner

REF = date(2026, 10, 21)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(tc, "shanghai_today", lambda: REF)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("今天", "2026-10-21"),
        ("明天", "2026-10-22"),
        ("后天", "2026-10-23"),
        ("大后天", "2026-10-24"),
        ("下周五", "2026-10-30"),
        ("下下周五", "2026-11-06"),
        ("周末", "2026-10-24"),
        ("下周末", "2026-10-31"),
        ("下个星期日", "2026-11-01"),
        ("2026-10-30", "2026-10-30"),
        ("2026/10/30", "2026-10-30"),
        ("2026年10月30日", "2026-10-30"),
        ("10月30日", "2026-10-30"),
        ("不是今天，是明天", "2026-10-22"),
        ("原定明天改为今天", "2026-10-21"),
        ("改成2026-10-31不是明天", "2026-10-31"),
        ("三天后", "2026-10-24"),
        ("昨天", "2026-10-20"),
    ],
)
def test_both_itinerary_parsers_share_date_resolution(text, expected):
    assert tc.resolve_date(text, REF)[0].isoformat() == expected
    assert planner.parse_start_date(text, REF)[0].isoformat() == expected
    assert reminder.parse_date_expression(text) == expected


@pytest.mark.parametrize("text", ["2026-02-30", "2月30日", "过几天", "明天或后天", "下个月"])
async def test_invalid_or_ambiguous_explicit_date_never_falls_back(monkeypatch, text):
    lookup = AsyncMock()
    collect = AsyncMock()
    monkeypatch.setattr(reminder.itinerary_service, "get_itinerary_by_date", lookup)
    monkeypatch.setattr(planner, "collect_candidates", collect)
    with pytest.raises(tc.DateResolutionError):
        await reminder.run(17, text)
    with pytest.raises(tc.DateResolutionError):
        await planner.generate(text, today=REF)
    lookup.assert_not_awaited()
    collect.assert_not_awaited()


def test_calendar_boundaries():
    assert tc.resolve_date("明天", date(2026, 12, 31))[0] == date(2027, 1, 1)
    assert tc.resolve_date("后天", date(2028, 2, 27))[0] == date(2028, 2, 29)
    assert tc.resolve_date("下周末", date(2026, 10, 25))[0] == date(2026, 10, 31)


def test_history_dates_keep_message_day_across_days():
    history = [{"role": "user", "content": "明天去广州塔", "date_reference": "2026-10-20"}]
    context = tc.build_temporal_context("那我穿什么", history, REF)
    assert context["dates"] == ["2026-10-21"]
    assert context["current_date"] == "2026-10-21"
    assert tc.build_temporal_context(
        "那天怎么穿", [{"role": "summary", "content": "用户说明天出发"}], REF
    )["unresolved"]


@pytest.mark.parametrize("text", ["2026-10-301", "10/30", "礼拜五", "30号"])
def test_unsupported_date_like_input_needs_clarification(text):
    with pytest.raises(tc.DateResolutionError):
        tc.resolve_date(text, REF)


def test_spaced_explicit_year_is_not_dropped():
    assert tc.resolve_date("2027 年 10 月 30 日", REF)[0] == date(2027, 10, 30)


@pytest.mark.parametrize("answer", ["明天（2026-10-21）广州晴", "2026-10-21（明天）广州晴"])
def test_explicit_relative_date_contradictions_are_deterministic(answer):
    context = tc.build_temporal_context("明天广州天气", ref=REF)
    assert tc.answer_date_issues(context, answer)
    assert not tc.answer_date_issues(context, "明天（2026-10-22）广州小雨")


@pytest.mark.parametrize(
    "question",
    [
        "明天穿什么，后天帮我安排广州一日游",
        "穿搭看明天，行程规划看后天",
    ],
)
def test_multiple_dates_bound_to_their_tasks(question):
    context = tc.build_temporal_context(question, ref=REF)
    assert tc.guard_tool_date("recommend_outfit", {}, context)["target_date"] == "2026-10-22"
    assert tc.guard_tool_date("plan_trip", {}, context)["target_date"] == "2026-10-23"
    with pytest.raises(tc.DateResolutionError):
        tc.guard_tool_date("recommend_outfit", {"target_date": "2026-10-23"}, context)


def test_comparison_requires_explicit_date_per_call():
    context = tc.build_temporal_context("对比明天和后天广州天气", ref=REF)
    with pytest.raises(tc.DateResolutionError):
        tc.guard_tool_date("get_forecast", {"city": "广州"}, context)
    for day in ("2026-10-22", "2026-10-23"):
        assert (
            tc.guard_tool_date("get_forecast", {"target_date": day}, context)["target_date"] == day
        )


def test_history_reference_and_current_correction():
    history = [
        {"role": "user", "content": "明天去广州塔"},
        {"role": "assistant", "content": "目标日期2026-10-21"},
    ]
    context = tc.build_temporal_context("那我穿什么", history, REF)
    assert context["source"] == "history_user" and context["dates"] == ["2026-10-22"]
    assert tc.build_temporal_context("改成后天", history, REF)["dates"] == ["2026-10-23"]
    assert tc.build_temporal_context("北京今天天气", history, REF)["dates"] == ["2026-10-21"]
    assert tc.build_temporal_context("北京天气", history, REF)["dates"] == []


def test_negative_reference_is_not_a_requested_date():
    context = tc.build_temporal_context("明天广州穿什么，不要把今天的炎热当成明天", ref=REF)
    assert context["dates"] == ["2026-10-22"]


@pytest.fixture
def weather(monkeypatch):
    def day(target, desc, low, high, rain):
        return SimpleNamespace(
            date=target, weather_desc=desc, temp_min=low, temp_max=high, precipitation_sum=rain
        )

    bundle = SimpleNamespace(
        daily=[
            day("2026-10-21", "晴", 33, 36, 0),
            day("2026-10-22", "小雨", 8, 12, 18),
            day("2026-10-23", "晴", -5, 0, 0),
        ],
        alerts=[],
    )
    for module in (tools, outfit_engine, route_planner, reminder):
        monkeypatch.setattr(module, "fetch_weather", AsyncMock(return_value=bundle))
    monkeypatch.setattr(outfit_engine.rag_service, "search", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        route_planner.amap_client,
        "plan_route",
        AsyncMock(
            return_value={
                "origin": "A",
                "destination": "B",
                "routes": [{"mode": "公交", "duration_min": 30, "distance_km": 10, "cost": 3}],
            }
        ),
    )
    return bundle


async def test_outfit_uses_target_date_and_preserves_zero_temperature(weather):
    data = await outfit_engine.outfit_structured(target_date="2026-10-22")
    assert data["weather_date"] == "2026-10-22"
    assert data["weather"]["temp"] == 12 and any("寒冷" in rule for rule in data["rules"])
    assert any("雨" in rule for rule in data["rules"])
    zero = await outfit_engine.outfit_structured(target_date="2026-10-23")
    assert zero["weather"]["temp"] == 0
    missing = await outfit_engine.outfit_structured(target_date="2026-10-30")
    assert missing["weather_date"] is None and missing["rules"] == []
    assert "暂无" in await outfit_engine.run(target_date="2026-10-30")


async def test_route_uses_target_day_and_excludes_missing_weather_from_scores(weather):
    data = await route_planner.plan_structured("A", "B", target_date="2026-10-22")
    assert data["weather_date"] == "2026-10-22" and data["weather"]["precip"] == 18
    missing = await route_planner.plan_structured("A", "B", target_date="2026-10-30")
    assert missing["weather_date"] is None and missing["routes"][0]["weather_score"] is None
    text = await route_planner.run("A", "B", target_date="2026-10-30")
    assert "未计入天气" in text and "36" not in text


async def test_weather_tools_filter_by_date_instead_of_slice_index(weather):
    for output in (
        await tools.get_weather("广州", target_date="2026-10-22"),
        await tools.get_forecast("广州", 1, target_date="2026-10-22"),
    ):
        assert "2026-10-22" in output and "8~12" in output and "36" not in output
    missing = await tools.get_forecast("广州", 1, target_date="2026-10-30")
    assert "暂无" in missing and '"weather_date": null' in missing and "36" not in missing


async def test_reminder_queries_exact_date_not_nearest(weather, monkeypatch):
    lookup = AsyncMock(
        return_value=[
            {
                "date": "2026-10-30",
                "title": "科技馆",
                "start_time": "11:53",
                "location": "广州",
                "activity": "参观",
            }
        ]
    )
    monkeypatch.setattr(reminder.itinerary_service, "get_itinerary_by_date", lookup)
    result = await reminder.run(17, "查我下周五已有行程")
    lookup.assert_awaited_once_with(17, "2026-10-30")
    assert "科技馆" in result and "11:53" in result and "暂无该日精确预报" in result


async def test_generated_plan_must_cover_requested_dates(weather, monkeypatch):
    monkeypatch.setattr(
        planner, "collect_candidates", AsyncMock(return_value={"indoor": [], "outdoor": []})
    )
    wrong = planner.TripPlan(city="广州", days=1, plan=[planner.PlanDay(date="2026-10-21")])
    monkeypatch.setattr(planner, "ainvoke_json", AsyncMock(return_value=wrong))
    with pytest.raises(tc.DateResolutionError, match="日期或天数"):
        await planner.generate("大后天广州一日游", today=REF)


def test_guard_does_not_mutate_args_or_allow_invented_date():
    args = {"city": "广州"}
    context = tc.build_temporal_context("明天天气", ref=REF)
    assert tc.guard_tool_date("get_weather", args, context)["target_date"] == "2026-10-22"
    assert args == {"city": "广州"}
    with pytest.raises(tc.DateResolutionError):
        tc.guard_tool_date(
            "get_weather", {"target_date": "2026-10-25"}, tc.build_temporal_context("天气", ref=REF)
        )
