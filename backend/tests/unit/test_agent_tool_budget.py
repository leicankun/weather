"""A deliberately looping model must stop at the tool-item budget."""

import asyncio

import httpx
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.main import app
from app.services import agent


class LoopModel(GenericFakeChatModel):
    batch_size: int = 1
    finish_after: int | None = None
    bad_date: bool = False
    unknown_tool: bool = False
    decisions: int = 0
    bindings: int = 0

    def bind_tools(self, tools, **kwargs):
        self.bindings += 1
        return self

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        message = self._generate(messages).generations[0].message
        yield ChatGenerationChunk(
            message=AIMessageChunk(content=message.content, tool_calls=message.tool_calls)
        )

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.decisions += 1
        used = sum(message.type == "tool" for message in messages)
        if self.finish_after is not None and used >= self.finish_after:
            response = AIMessage(content="查询完成")
        else:
            args = {"city": "广州"}
            if self.bad_date:
                args["target_date"] = "2026-10-21"
            response = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "unknown_weather" if self.unknown_tool else "get_weather",
                        "args": args,
                        "id": f"call-{used}-{index}",
                    }
                    for index in range(self.batch_size)
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=response)])


@pytest.fixture(autouse=True)
def reset_graph():
    agent._agent_instance = None
    yield
    agent._agent_instance = None


def install(monkeypatch, model, *, limit=3):
    calls = []

    @tool
    async def get_weather(city: str, target_date: str | None = None) -> str:
        """Fixture weather provider."""
        await asyncio.sleep(0)  # Let batch/concurrent invocations actually interleave.
        calls.append((city, target_date))
        return "fixture weather"

    async def tools():
        return [get_weather]

    monkeypatch.setattr(settings, "AGENT_MAX_TOOL_CALLS", limit)
    monkeypatch.setattr(agent, "use_multi_agent", lambda: False)
    monkeypatch.setattr(agent, "get_llm", lambda: model)
    monkeypatch.setattr(agent, "_get_all_tools", tools)
    return calls


@pytest.mark.parametrize(
    ("batch_size", "limit", "expected"), [(1, 3, 3), (2, 4, 4), (2, 3, 2), (5, 3, 0)]
)
async def test_budget_counts_individual_tools_and_rejects_overflow_batch(
    monkeypatch, batch_size, limit, expected
):
    model = LoopModel(messages=iter(()), batch_size=batch_size)
    calls = install(monkeypatch, model, limit=limit)
    graph = await agent._build_graph()
    result = await graph.ainvoke(agent._build_initial_state("广州天气"))
    assert len(calls) == result["tool_call_count"] == expected
    assert result["tool_limit_exceeded"] is True
    assert result["answer"] == agent.tool_limit_answer(limit)
    assert result["messages"][-1].content == result["answer"]
    requested = [
        call["id"] for message in result["messages"] for call in getattr(message, "tool_calls", [])
    ]
    resolved = [message.tool_call_id for message in result["messages"] if message.type == "tool"]
    assert sorted(requested) == sorted(resolved)
    assert model.decisions == expected // batch_size + 1


async def test_exact_budget_still_allows_final_answer_without_tools(monkeypatch):
    model = LoopModel(messages=iter(()), finish_after=3)
    calls = install(monkeypatch, model)
    graph = await agent._build_graph()
    result = await graph.ainvoke(agent._build_initial_state("广州天气"))
    assert len(calls) == 3 and result["answer"] == "查询完成"
    assert result["tool_limit_exceeded"] is False
    assert model.bindings == 3 and model.decisions == 4


@pytest.mark.parametrize("unknown_tool", [False, True])
async def test_failed_validation_and_unknown_tool_requests_consume_budget(
    monkeypatch, unknown_tool
):
    from datetime import date

    from app.services import temporal_context

    monkeypatch.setattr(temporal_context, "shanghai_today", lambda: date(2026, 10, 21))
    model = LoopModel(messages=iter(()), bad_date=not unknown_tool, unknown_tool=unknown_tool)
    calls = install(monkeypatch, model)
    graph = await agent._build_graph()
    result = await graph.ainvoke(agent._build_initial_state("明天广州天气"))
    assert calls == [] and result["tool_call_count"] == 3
    assert result["answer"] == agent.tool_limit_answer(3)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("dated", [False, True])
async def test_http_and_sse_return_explicit_limit_answer(monkeypatch, stream, dated):
    model = LoopModel(messages=iter(()))
    calls = install(monkeypatch, model)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/chat" + ("/stream" if stream else ""),
            json={"message": "明天广州天气" if dated else "广州天气"},
        )
    assert response.status_code == 200
    if stream:
        import json

        events = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        assert [event["content"] for event in events if event["type"] == "token"] == [
            agent.tool_limit_answer(3)
        ]
        assert events[-1]["type"] == "done"
    else:
        assert response.json()["data"]["answer"] == agent.tool_limit_answer(3)
    assert len(calls) == 3


async def test_parallel_conversations_and_new_requests_have_independent_budgets(monkeypatch):
    calls = install(monkeypatch, LoopModel(messages=iter(())))
    answers = await asyncio.gather(agent.chat("广州天气"), agent.chat("北京天气"))
    assert len(calls) == 6 and all(
        answer["answer"] == agent.tool_limit_answer(3) for answer in answers
    )
    result = await agent.chat("广州天气")
    assert len(calls) == 9 and result["answer"] == agent.tool_limit_answer(3)


async def test_budget_is_reserved_before_any_batch_business_io(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    calls = install(monkeypatch, LoopModel(messages=iter(()), batch_size=2), limit=2)
    graph = await agent._build_graph(InMemorySaver())
    graph.interrupt_before_nodes = ["tools"]
    config = {"configurable": {"thread_id": "reserved-budget"}}
    result = await graph.ainvoke(agent._build_initial_state("广州天气"), config=config)
    assert calls == [] and result["tool_call_count"] == 2
    snapshot = await graph.aget_state(config)
    assert snapshot.next == ("tools",)
    # Resuming does not reset or double-reserve the budget; the following batch stops.
    monkeypatch.setattr(settings, "AGENT_MAX_TOOL_CALLS", 10)
    result = await graph.ainvoke(None, config=config)
    assert len(calls) == 2 and result["tool_call_count"] == 2
    assert result["tool_call_limit"] == 2 and result["tool_limit_exceeded"]


@pytest.mark.parametrize("value", [0, -1, 101])
def test_configuration_rejects_invalid_budgets(value):
    with pytest.raises(ValidationError):
        Settings(AGENT_MAX_TOOL_CALLS=value)
