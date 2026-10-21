"""PostgreSQL recovery must retain the budget, and a new turn must reset it."""

import json

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.chat_message import ChatMessage
from app.models.user import User
from app.services import agent, checkpoint_service
from tests.unit.test_agent_tool_budget import LoopModel, install


@pytest.mark.parametrize("stream", [False, True])
async def test_resume_retains_budget_and_new_turn_starts_fresh(
    client, auth_headers, db, monkeypatch, stream
):
    monkeypatch.setattr(settings, "CHECKPOINT_ENABLED", True)
    model = LoopModel(messages=iter(()))
    calls = install(monkeypatch, model, limit=2)
    original = agent._agent_node
    fail = True

    async def crash_after_saved_tool(state):
        nonlocal fail
        if fail and state["messages"][-1].type == "tool":
            fail = False
            raise RuntimeError("injected failure after first tool checkpoint")
        return await original(state)

    monkeypatch.setattr(agent, "_agent_node", crash_after_saved_tool)
    agent._agent_instance = None
    path = "/api/v1/chat/stream" if stream else "/api/v1/chat"
    response = await client.post(
        path, headers=auth_headers, json={"message": "广州天气", "conversation_id": "budget-resume"}
    )
    if stream:
        events = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        assert events[-1]["type"] == "error"
    else:
        assert response.status_code == 503
    assert len(calls) == 1
    owner = await db.scalar(select(User.id).where(User.username == "tester"))

    async def snapshot():
        async with checkpoint_service.open_thread(
            owner, "budget-resume", "single", resume=True
        ) as context:
            graph = await agent._build_graph(context.saver)
            return await graph.aget_state(context.config)

    saved = await snapshot()
    assert saved.values["tool_call_count"] == 1 and saved.next == ("agent",)
    monkeypatch.setattr(settings, "AGENT_MAX_TOOL_CALLS", 10)
    for _ in range(2):
        resumed = await client.post(
            "/api/v1/chat/conversations/budget-resume/resume", headers=auth_headers
        )
        assert resumed.status_code == 200
        assert resumed.json()["data"]["answer"] == agent.tool_limit_answer(2)
    assert len(calls) == 2
    saved = await snapshot()
    assert saved.values["tool_call_count"] == saved.values["tool_call_limit"] == 2
    assert saved.values["tool_limit_exceeded"] and not saved.next
    assert await db.scalar(select(func.count()).select_from(ChatMessage)) == 2

    monkeypatch.setattr(settings, "AGENT_MAX_TOOL_CALLS", 3)
    response = await client.post(
        "/api/v1/chat",
        headers=auth_headers,
        json={"message": "再查广州天气", "conversation_id": "budget-resume"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["answer"] == agent.tool_limit_answer(3)
    assert len(calls) == 5
    saved = await snapshot()
    assert saved.values["tool_call_count"] == saved.values["tool_call_limit"] == 3
    assert await db.scalar(select(func.count()).select_from(ChatMessage)) == 4
    agent._agent_instance = None
