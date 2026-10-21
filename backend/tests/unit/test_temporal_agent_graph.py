"""Real single/multi graphs and HTTP/SSE with deterministic model/provider stubs."""

import json
from datetime import date

import httpx
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool
from pydantic import Field

from app.main import app
from app.services import agent, multi_agent
from app.services import temporal_context as tc
from app.services.answer_verification import VERIFICATION_UNAVAILABLE


class DateModel(GenericFakeChatModel):
    inputs: list = Field(default_factory=list)
    reviews: list = Field(default_factory=list)
    review_error: bool = False

    def bind_tools(self, tools, **kwargs):
        return self

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        message = self._generate(messages).generations[0].message
        yield ChatGenerationChunk(
            message=AIMessageChunk(content=message.content, tool_calls=message.tool_calls)
        )

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        system = messages[0].content
        if "独立的回答验证员" in system:
            payload = json.loads(messages[-1].content)
            self.reviews.append(payload)
            if self.review_error:
                raise RuntimeError("test verifier unavailable")
            if len(self.reviews) == 1:
                content = json.dumps(
                    {
                        "passed": False,
                        "issues": ["日期混用"],
                        "revised_answer": "明天（2026-10-22）广州小雨，8~12℃，请保暖带伞。",
                    },
                    ensure_ascii=False,
                )
            else:
                content = '{"passed":true,"issues":[],"revised_answer":""}'
            response = AIMessage(content=content)
        else:
            self.inputs.append(messages)
            last = messages[-1]
            if last.type != "tool":
                # Deliberately wrong model argument: must never reach business IO.
                response = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "get_forecast",
                            "args": {"city": "广州", "target_date": "2026-10-21"},
                            "id": "bad-date",
                        }
                    ],
                )
            elif "与该任务不符" in last.content:
                response = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "get_forecast",
                            "args": {"city": "广州", "target_date": "2026-10-22"},
                            "id": "good-date",
                        }
                    ],
                )
            else:
                response = AIMessage(content="错误草稿：明天2026-10-21广州晴，36℃，不用带伞。")
        return ChatResult(generations=[ChatGeneration(message=response)])


@pytest.mark.parametrize("mode", ["single", "multi"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("review_error", [False, True])
async def test_http_and_sse_refuse_wrong_argument_and_publish_only_verified_answer(
    monkeypatch, mode, stream, review_error
):
    monkeypatch.setattr(tc, "shanghai_today", lambda: date(2026, 10, 21))
    model = DateModel(messages=iter(()), review_error=review_error)
    calls = []

    @tool
    async def get_forecast(city: str, target_date: str | None = None) -> str:
        """Return exact-date fixture weather."""
        calls.append((city, target_date))
        return (
            "requested_date=2026-10-22 effective_date=2026-10-22 weather_date=2026-10-22 小雨8~12℃"
        )

    async def all_tools():
        return [get_forecast]

    async def domain_tools():
        return {"weather": [get_forecast]}

    monkeypatch.setattr(agent, "use_multi_agent", lambda: mode == "multi")
    monkeypatch.setattr(agent, "get_llm", lambda: model)
    monkeypatch.setattr(multi_agent, "get_llm", lambda: model)
    monkeypatch.setattr(agent, "_get_all_tools", all_tools)
    monkeypatch.setattr(multi_agent, "load_domain_tools", domain_tools)
    monkeypatch.setattr(agent, "_agent_instance", None)
    monkeypatch.setattr(multi_agent, "_graph", None)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/chat" + ("/stream" if stream else ""),
                json={"message": "广州明天天气", "conversation_id": "date-regression"},
            )
        assert response.status_code == 200
        expected = (
            VERIFICATION_UNAVAILABLE
            if review_error
            else "明天（2026-10-22）广州小雨，8~12℃，请保暖带伞。"
        )
        if stream:
            events = [
                json.loads(line.removeprefix("data: "))
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            answer = "".join(
                event.get("content", "") for event in events if event["type"] == "token"
            )
            assert events[-1]["type"] == "done"
            assert "错误草稿" not in response.text and "passed" not in response.text
        else:
            answer = response.json()["data"]["answer"]
        assert answer == expected
        assert calls == [("广州", "2026-10-22")]
        assert model.reviews[0]["temporal_context"]["dates"] == ["2026-10-22"]
        assert any(
            "日期校验" in str(message.content) or "与该任务不符" in str(message.content)
            for messages in model.inputs
            for message in messages
        )
    finally:
        agent._agent_instance = None
        multi_agent.reset_cache()


async def test_multi_supervisor_passes_one_shared_date_context_to_parallel_agents(monkeypatch):
    from app.services.multi_agent import DomainRun

    context = tc.build_temporal_context("明天穿什么，后天安排一日游", ref=date(2026, 10, 21))
    result = await multi_agent._supervisor_node(
        {
            "question": "明天穿什么，后天安排一日游",
            "domains": ["outfit", "itinerary"],
            "temporal_context": context,
        }
    )
    assert result["temporal_context"] == context
    received = []

    async def runner(key, question, tools, **kwargs):
        received.append((key, kwargs["temporal_context"]))
        return DomainRun(answer="fixture")

    async def loader():
        return {"outfit": [], "itinerary": []}

    monkeypatch.setattr(multi_agent, "run_domain_agent", runner)
    monkeypatch.setattr(multi_agent, "load_domain_tools", loader)
    for key in ("outfit", "itinerary"):
        await multi_agent._make_domain_node(key)(
            {"question": "明天穿什么，后天安排一日游", **result}
        )
    assert len(received) == 2 and all(value == context for _, value in received)


async def test_checkpoint_resume_keeps_original_anchor_after_midnight(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    monkeypatch.setattr(tc, "shanghai_today", lambda: date(2026, 10, 21))
    model = DateModel(messages=iter(()))
    calls = []

    @tool
    async def get_forecast(city: str, target_date: str | None = None) -> str:
        """Exact-date weather fixture."""
        calls.append(target_date)
        return "2026-10-22 小雨8~12℃"

    async def tools():
        return [get_forecast]

    monkeypatch.setattr(agent, "get_llm", lambda: model)
    monkeypatch.setattr(agent, "_get_all_tools", tools)
    graph = await agent._build_graph(InMemorySaver())
    graph.interrupt_before_nodes = ["tools"]
    config = {"configurable": {"thread_id": "midnight-date"}}
    await graph.ainvoke(agent._build_initial_state("明天广州天气"), config=config)
    monkeypatch.setattr(tc, "shanghai_today", lambda: date(2026, 10, 22))
    await graph.ainvoke(None, config=config)  # Reject the old wrong argument.
    result = await graph.ainvoke(None, config=config)  # Execute the corrected call.
    assert result["temporal_context"]["current_date"] == "2026-10-21"
    assert result["temporal_context"]["dates"] == ["2026-10-22"]
    assert calls == ["2026-10-22"]
    assert result["verification"]["status"] == "corrected"


async def test_mistaken_verifier_cannot_release_explicit_date_contradiction():
    from unittest.mock import AsyncMock

    from app.services.answer_verification import VERIFICATION_FAILED, verify_draft

    llm = AsyncMock()
    llm.ainvoke.return_value = AIMessage(content='{"passed":true,"issues":[],"revised_answer":""}')
    result = await verify_draft(
        {
            "question": "明天广州天气",
            "answer": "明天（2026-10-21）广州晴",
            "temporal_context": tc.build_temporal_context("明天广州天气", ref=date(2026, 10, 21)),
        },
        lambda: llm,
    )
    assert result["answer"] == VERIFICATION_FAILED
    assert result["verification"]["checks"] == 2
    assert "2026-10-22" in result["verification"]["issues"][0]
