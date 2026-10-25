"""Benchmark grading must not hide failures or demand a single tool sequence."""

import copy

import pytest

from app.scripts.agent_benchmark import judge, token_totals, tool_score
from app.scripts.agent_benchmark_data import cases, day, fixture_result
from app.scripts.report_agent_benchmark import aggregate, percentile
from app.scripts.score_agent_benchmark_offline import score, unavailable


def case(key):
    return next(c for c in cases() if c["id"] == key)


def test_dataset_is_unique_and_balanced_and_has_explicit_rubrics():
    rows = cases()
    assert len(rows) == len({r["id"] for r in rows}) == 40
    assert {r["category"] for r in rows} == {
        "weather",
        "outfit",
        "route",
        "knowledge",
        "composite",
        "followup",
    }
    assert all(
        r["criteria"]
        and set().union(*map(set, r["required_tool_groups"])).issubset(r["allowed_tools"])
        for r in rows
    )


def test_future_weather_does_not_silently_fall_back_to_today():
    row = case("w07")
    output = fixture_result("get_weather", {"city": "广州", "target_date": day(30)}, row)
    assert output["weather_status"] == "unavailable"
    assert output["weather"]["available"] is False
    assert "temp_max_c" not in output["weather"]


def test_tool_score_accepts_alternative_weather_tool_but_rejects_wrong_effective_date():
    row = case("w02")
    trace = [{"name": "get_forecast", "args": {"city": "广州市", "target_date": day(1)}}]
    assert tool_score(row, trace, [])["all_required_covered"]
    wrong = copy.deepcopy(trace)
    wrong[0]["args"]["target_date"] = day(0)
    score = tool_score(row, wrong, [])
    assert not score["all_required_covered"]
    assert "wrong_target_date" in score["details"][0]["issues"]


def test_optional_weather_call_is_not_unnecessary_for_outfit():
    row = case("o01")
    trace = [
        {"name": name, "args": {"city": "广州", "target_date": day(1)}}
        for name in ["get_weather", "recommend_outfit"]
    ]
    result = tool_score(row, trace, [])
    assert result["all_required_covered"] and result["unnecessary_calls"] == 0


def test_model_attempts_rejected_before_execution_remain_counted():
    row = case("w02")
    result = tool_score(
        row, [], [{"tool_calls": [{"name": "get_weather", "args": {"target_date": day(0)}}]}]
    )
    assert result["attempted_calls"] == 1 and result["attempted_not_executed"] == 1
    assert not result["all_required_covered"]


def test_missing_usage_is_unknown_and_chat_embedding_units_are_separate():
    rows = [
        {
            "kind": "chat",
            "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
        },
        {"kind": "chat", "error": "timeout"},
        {"kind": "embedding", "usage": {"total_tokens": 5}},
    ]
    chat = token_totals(rows, "chat")
    assert chat["total_tokens"] == 13 and chat["attempts"] == 2 and chat["missing_usage_calls"] == 1
    assert token_totals(rows, "embedding")["total_tokens"] == 5


@pytest.mark.asyncio
async def test_empty_failed_answers_receive_zero_without_calling_grader():
    class NeverCall:
        async def ainvoke(self, *args):
            raise AssertionError("must not grade an empty answer")

    result = await judge({"answer": "", "error": "TimeoutError"}, case("w01"), NeverCall())
    assert result["task_success"] is False and result["covered"] == 0


@pytest.mark.asyncio
async def test_grader_cannot_omit_failed_criteria_or_use_string_boolean():
    class InvalidGrade:
        async def ainvoke(self, *args):
            class Response:
                content = '{"criteria":[],"behavior_correct":"true","unsupported_claims":[],"contradictions":[]}'

            return Response()

    result = await judge({"answer": "test", "tool_executions": []}, case("w01"), InvalidGrade())
    assert result["status"] == "judge_failed" and not result["task_success"]


def test_route_cost_is_not_a_taxi_quote_and_faults_have_no_fabricated_facts():
    result = fixture_result(
        "plan_travel_route", {"origin": "广州南站", "destination": "广州塔"}, case("r02")
    )
    assert result["taxi_quote"] is None and result["driving"]["estimated_driving_cost_yuan"] == 10.3
    failed = fixture_result("plan_travel_route", {}, case("r06"))
    assert "error" in failed and "metro" not in failed


def test_composite_weather_coverage_accepts_outfit_weather_evidence():
    row = case("m01")
    trace = [{"name": "recommend_outfit", "args": {"city": "广州", "target_date": day(1)}}]
    assert tool_score(row, trace, [])["all_required_covered"]


def test_summary_retains_failed_trials_and_missing_usage_in_denominators():
    sample = {
        "case_id": "w01",
        "answer": "",
        "error": "TimeoutError",
        "graph_ms": 1000,
        "models": [],
        "quality": {
            "status": "unavailable_answer",
            "task_success": False,
            "covered": 0,
            "total": 1,
        },
        "tools": tool_score(case("w01"), [], []),
        "chat_usage": token_totals([{"kind": "chat", "error": "TimeoutError"}], "chat"),
        "embedding_usage": token_totals([], "embedding"),
    }
    result = aggregate([sample])
    assert result["runs"] == 1 and result["task_pass_rate"] == 0
    assert result["execution_errors"] == 1 and result["criteria_total"] == 1
    assert result["chat_calls_missing_usage"] == 1
    assert result["tool_parameter_correct_rate"] is None
    assert percentile([0, 100], 0.95) == 95


def test_scoring_service_failure_does_not_become_an_agent_quality_failure_rate():
    sample = {
        "case_id": "w01",
        "answer": "广州多云24~31℃",
        "graph_ms": 1000,
        "models": [],
        "quality": {"status": "judge_failed", "task_success": False, "covered": 0, "total": 1},
        "tools": tool_score(case("w01"), [], []),
        "chat_usage": token_totals([], "chat"),
        "embedding_usage": token_totals([], "embedding"),
    }
    result = aggregate([sample])
    assert result["task_pass_rate"] is None and result["criteria_coverage"] is None
    assert not result["quality_complete"] and result["quality_evaluated_runs"] == 0


def test_offline_refusal_does_not_pass_when_it_then_fabricates_route_figures():
    row = {
        "case_id": "r06",
        "answer": "路线服务超时，无法确认。不过可以坐2号线，大约40分钟6元。",
        "tool_executions": [],
    }
    assert not score(row)["all_keypoints_pass"]
    row["answer"] = "路线服务超时，未能获取本次时间或票价，不能确认。"
    assert score(row)["all_keypoints_pass"]
    assert unavailable("最高温查不到") and unavailable("没有查到营业时间")


def test_offline_followup_does_not_require_unasked_driving_figures():
    row = {
        "case_id": "f02",
        "answer": "从广州南站到广州塔，地铁约51分钟、5元。",
        "tool_executions": [],
    }
    assert score(row)["all_keypoints_pass"]


def test_offline_contextless_clarification_must_not_assume_route_domain():
    row = {
        "case_id": "f06",
        "answer": "你想问哪一段路线？请告诉我出发地和目的地。",
        "tool_executions": [],
    }
    assert not score(row)["all_keypoints_pass"]
    row["answer"] = "你具体想问什么？可以补充天气、穿搭或路线等问题。"
    assert score(row)["all_keypoints_pass"]


@pytest.mark.asyncio
async def test_balance_exhaustion_is_terminal_for_grading_not_retried():
    class PaymentError(Exception):
        status_code = 402

    class ExhaustedModel:
        calls = 0

        async def ainvoke(self, *args):
            self.calls += 1
            raise PaymentError("insufficient balance")

    model = ExhaustedModel()
    result = await judge({"answer": "test", "tool_executions": []}, case("w01"), model)
    assert model.calls == 1
    assert result["status"] == "judge_failed" and result["terminal_service_error"]
