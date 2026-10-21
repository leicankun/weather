"""Real PostgreSQL checkpoint lifecycle; fake models isolate orchestration failures."""

import asyncio
import json
from datetime import date

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from sqlalchemy import func, select, text

from app.core.config import settings
from app.core.security import create_access_token
from app.models.chat_message import ChatMessage
from app.models.checkpoint import AgentCheckpointThread
from app.models.user import User
from app.services import (
    agent,
    chat_history_service,
    checkpoint_service,
    multi_agent,
    temporal_context,
)
from tests.api import test_chat_summary as summary_tests
from tests.api import test_memory as memory_tests

ask = memory_tests.ask


@pytest.fixture
def memory_enabled(monkeypatch):
    return memory_tests.memory_enabled.__wrapped__(monkeypatch)


@pytest.fixture
def memory_model(monkeypatch, memory_enabled):
    yield from memory_tests.memory_model.__wrapped__(monkeypatch, memory_enabled)


@pytest.fixture(autouse=True)
def checkpoints_enabled(monkeypatch):
    monkeypatch.setattr(settings, "CHECKPOINT_ENABLED", True)


async def owner_id(db):
    return await db.scalar(select(User.id).where(User.username == "tester"))


async def state(user_id, conversation_id, mode):
    # A fresh connection and compiled graph simulate application restart.
    async with checkpoint_service.open_thread(user_id, conversation_id, mode, resume=True) as ctx:
        graph = await (
            multi_agent._build_graph(ctx.saver)
            if mode == "multi"
            else agent._build_graph(ctx.saver)
        )
        return await graph.aget_state(ctx.config)


@pytest.mark.parametrize("mode", ["single", "multi"])
@pytest.mark.parametrize("path", ["/api/v1/chat", "/api/v1/chat/stream"])
async def test_checkpoint_coexists_with_cards_and_history_rag(
    client,
    auth_headers,
    db,
    memory_model,
    monkeypatch,
    mode,
    path,
):
    monkeypatch.setattr(settings, "AGENT_MODE", mode)
    await ask(client, path, "我长期住在天河区，我不吃辣。", "cards", auth_headers)
    owner = await owner_id(db)
    await chat_history_service.add_message(owner, "old", "user", "那次活动的暗号是蓝鲸7429")
    for index in range(12):
        await chat_history_service.add_message(owner, "old", "assistant", f"无关闲聊{index}")
    answer = await ask(client, path, "之前活动的暗号是什么", "new", auth_headers)
    assert "不吃辣" in answer and "蓝鲸7429" in answer
    snapshot = await state(owner, "new", mode)
    assert not snapshot.next
    assert "不吃辣" in snapshot.values["memory_context"]
    assert "蓝鲸7429" in snapshot.values["memory_context"]
    assert snapshot.values["answer"] == answer
    status = await client.get(
        f"/api/v1/chat/conversations/new/checkpoint?mode={mode}", headers=auth_headers
    )
    assert status.json()["data"]["status"] == "saved"
    assert len(status.json()["data"]["snapshots"]) >= 3


@pytest.mark.parametrize("mode", ["single", "multi"])
async def test_new_turn_replaces_reducers_and_does_not_duplicate_context(
    client,
    auth_headers,
    db,
    memory_model,
    monkeypatch,
    mode,
):
    monkeypatch.setattr(settings, "AGENT_MODE", mode)
    await ask(client, "/api/v1/chat", "明天天气和穿什么", "same", auth_headers)
    owner = await owner_id(db)
    old = await state(owner, "same", mode)
    await ask(client, "/api/v1/chat", "你好", "same", auth_headers)
    new = await state(owner, "same", mode)
    assert old.values["run_id"] != new.values["run_id"]
    if mode == "single":
        assert [m.type for m in new.values["messages"]] == ["human", "ai", "human", "ai"]
    else:
        assert new.values["domains"] == []
        assert new.values["domain_answers"] == new.values["domain_evidence"] == {}
        assert new.values["verification"] == {}
        assert new.values["metrics"]["llm_calls"] == 1
        assert len(new.values["history"]) == 2


@pytest.mark.parametrize("stream", [False, True])
async def test_resume_after_tool_step_and_idempotent_completion(
    client,
    auth_headers,
    db,
    monkeypatch,
    stream,
):
    monkeypatch.setattr(settings, "AGENT_MODE", "single")
    monkeypatch.setattr(temporal_context, "shanghai_today", lambda: date(2026, 10, 21))
    review_model = summary_tests.ContextModel(messages=iter(()))
    monkeypatch.setattr(agent, "get_llm", lambda: review_model)
    calls = {"tool": 0, "fail": True}

    @tool
    async def get_weather(target_date: str | None = None):
        """Return test weather."""
        assert target_date == "2026-10-21"
        calls["tool"] += 1
        return "23度"

    async def tools():
        return [get_weather]

    async def generate(current):
        if current["messages"][-1].type == "tool":
            if calls["fail"]:
                calls["fail"] = False
                raise RuntimeError("injected failure after saved tool step")
            return {"messages": [AIMessage(content="23度")], "answer": "23度"}
        return {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "get_weather",
                            "args": {},
                            "id": "weather-call",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        }

    monkeypatch.setattr(agent, "_get_all_tools", tools)
    monkeypatch.setattr(agent, "_agent_node", generate)
    path = "/api/v1/chat/stream" if stream else "/api/v1/chat"
    response = await client.post(
        path, headers=auth_headers, json={"message": "今天天气", "conversation_id": "crash"}
    )
    if stream:
        events = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        assert events[-1]["type"] == "error" and all(e["type"] != "done" for e in events)
    else:
        assert response.status_code == 503
    assert calls["tool"] == 1
    owner = await owner_id(db)
    snapshot = await state(owner, "crash", "single")
    assert snapshot.next == ("agent",)
    assert snapshot.values["messages"][-1].type == "tool"
    assert snapshot.values["temporal_context"]["dates"] == ["2026-10-21"]
    monkeypatch.setattr(temporal_context, "shanghai_today", lambda: date(2026, 10, 22))
    blocked = await client.post(
        "/api/v1/chat", headers=auth_headers, json={"message": "新问题", "conversation_id": "crash"}
    )
    assert blocked.status_code == 409
    monkeypatch.setattr(settings, "AGENT_MODE", "multi")
    blocked = await client.post(
        "/api/v1/chat", headers=auth_headers, json={"message": "新问题", "conversation_id": "crash"}
    )
    assert blocked.status_code == 409
    for _ in range(2):
        resumed = await client.post("/api/v1/chat/conversations/crash/resume", headers=auth_headers)
        assert resumed.status_code == 200
        assert resumed.json()["data"]["answer"] == "23度"
    assert calls["tool"] == 1
    assert await db.scalar(select(func.count()).select_from(ChatMessage)) == 2


async def test_parallel_resume_preserves_successful_domain_pending_writes(
    client,
    auth_headers,
    db,
    memory_model,
    monkeypatch,
):
    monkeypatch.setattr(settings, "AGENT_MODE", "multi")
    calls = {"weather": 0, "outfit": 0}

    async def run(key, *args, **kwargs):
        calls[key] += 1
        if key == "outfit" and calls[key] == 1:
            await asyncio.sleep(0.05)
            raise RuntimeError("injected outfit failure")
        return multi_agent.DomainRun(answer=f"{key}结果", llm_calls=1)

    monkeypatch.setattr(multi_agent, "run_domain_agent", run)
    response = await client.post(
        "/api/v1/chat",
        headers=auth_headers,
        json={"message": "今天天气和穿什么", "conversation_id": "parallel"},
    )
    assert response.status_code == 503
    assert calls == {"weather": 1, "outfit": 1}
    # Even if the default changes, recovery uses the saved mode explicitly.
    monkeypatch.setattr(settings, "AGENT_MODE", "single")
    response = await client.post(
        "/api/v1/chat/conversations/parallel/resume?mode=multi", headers=auth_headers
    )
    assert response.status_code == 200 and response.json()["code"] == 0
    assert calls == {"weather": 1, "outfit": 2}
    snapshot = await state(await owner_id(db), "parallel", "multi")
    assert set(snapshot.values["domain_answers"]) == {"weather", "outfit"}
    assert snapshot.values["verification"]["status"] == "passed"
    assert await db.scalar(select(func.count()).select_from(ChatMessage)) == 2


async def test_user_session_mode_isolation_and_reset_preserves_history(
    client,
    auth_headers,
    db,
    memory_model,
    monkeypatch,
):
    owner = await owner_id(db)
    other = User(username="checkpoint_other", password_hash="unused")
    db.add(other)
    await db.commit()
    other_headers = {"Authorization": f"Bearer {create_access_token(other.id)}"}
    for mode in ("single", "multi"):
        monkeypatch.setattr(settings, "AGENT_MODE", mode)
        await ask(client, "/api/v1/chat", "你好", "shared", auth_headers)
    assert await db.scalar(select(func.count()).select_from(AgentCheckpointThread)) == 2
    assert (
        await client.get("/api/v1/chat/conversations/shared/checkpoint", headers=other_headers)
    ).status_code == 404
    assert (
        await client.post("/api/v1/chat/conversations/shared/resume", headers=other_headers)
    ).status_code == 404
    assert (
        await client.get("/api/v1/chat/conversations/absent/checkpoint", headers=auth_headers)
    ).status_code == 404
    await ask(client, "/api/v1/chat", "你好", "shared", None)
    assert await db.scalar(select(func.count()).select_from(AgentCheckpointThread)) == 2
    await client.delete("/api/v1/chat/conversations/shared/checkpoint", headers=auth_headers)
    assert await db.scalar(select(func.count()).select_from(AgentCheckpointThread)) == 1
    assert len(await chat_history_service.get_recent_history(owner, "shared")) == 4
    # History still feeds a fresh thread after checkpoint reset.
    monkeypatch.setattr(settings, "AGENT_MODE", "single")
    await ask(client, "/api/v1/chat", "继续", "shared", auth_headers)
    response = await client.delete("/api/v1/chat/conversations/shared", headers=auth_headers)
    assert response.status_code == 200
    for name in (
        "agent_checkpoint_threads",
        "checkpoints",
        "checkpoint_writes",
        "checkpoint_blobs",
    ):
        assert await db.scalar(text(f"SELECT count(*) FROM {name}")) == 0


async def test_concurrent_request_and_delete_do_not_corrupt_active_thread(
    client,
    auth_headers,
    db,
    memory_model,
    monkeypatch,
):
    monkeypatch.setattr(settings, "AGENT_MODE", "single")
    started, release = asyncio.Event(), asyncio.Event()
    original = agent._agent_node

    async def paused(current):
        started.set()
        await release.wait()
        return await original(current)

    monkeypatch.setattr(agent, "_agent_node", paused)
    running = asyncio.create_task(
        client.post(
            "/api/v1/chat",
            headers=auth_headers,
            json={"message": "你好", "conversation_id": "busy"},
        )
    )
    try:
        await asyncio.wait_for(started.wait(), 10)
        second = await client.post(
            "/api/v1/chat",
            headers=auth_headers,
            json={"message": "第二问", "conversation_id": "busy"},
        )
        assert second.status_code == 409
        deleting = await client.delete("/api/v1/chat/conversations/busy", headers=auth_headers)
        assert deleting.status_code == 409
    finally:
        release.set()
        assert (await running).status_code == 200
    assert await db.scalar(select(func.count()).select_from(ChatMessage)) == 2
    assert (
        await client.delete("/api/v1/chat/conversations/busy", headers=auth_headers)
    ).status_code == 200


async def test_graph_completed_but_message_save_failed_is_recoverable(
    client,
    auth_headers,
    db,
    memory_model,
    monkeypatch,
):
    monkeypatch.setattr(settings, "AGENT_MODE", "single")
    original = checkpoint_service.save_turn

    async def fail(*args):
        raise RuntimeError("injected crash before message commit")

    monkeypatch.setattr(checkpoint_service, "save_turn", fail)
    response = await client.post(
        "/api/v1/chat",
        headers=auth_headers,
        json={"message": "你好", "conversation_id": "finished"},
    )
    assert response.status_code == 503
    assert await db.scalar(select(func.count()).select_from(ChatMessage)) == 0
    model_calls = len(memory_model.calls)
    monkeypatch.setattr(checkpoint_service, "save_turn", original)
    for _ in range(2):
        response = await client.post(
            "/api/v1/chat/conversations/finished/resume", headers=auth_headers
        )
        assert response.status_code == 200
    assert len(memory_model.calls) == model_calls
    assert await db.scalar(select(func.count()).select_from(ChatMessage)) == 2


async def test_disabling_checkpointer_keeps_existing_memory_path(
    client,
    auth_headers,
    db,
    memory_model,
    monkeypatch,
):
    monkeypatch.setattr(settings, "CHECKPOINT_ENABLED", False)
    await ask(client, "/api/v1/chat", "我不吃辣。", "disabled", auth_headers)
    answer = await ask(client, "/api/v1/chat", "结合我的情况说两句", "second", auth_headers)
    assert "不吃辣" in answer
    assert await db.scalar(select(func.count()).select_from(AgentCheckpointThread)) == 0


@pytest.mark.parametrize("mode", ["single", "multi"])
async def test_empty_model_reply_leaves_retryable_node(
    client,
    auth_headers,
    db,
    memory_model,
    monkeypatch,
    mode,
):
    monkeypatch.setattr(settings, "AGENT_MODE", mode)
    original = memory_tests.MemoryModel._generate
    fail = True

    def generate(self, messages, *args, **kwargs):
        nonlocal fail
        if fail and "意图识别模块" not in messages[0].content:
            fail = False
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=""))])
        return original(self, messages, *args, **kwargs)

    monkeypatch.setattr(memory_tests.MemoryModel, "_generate", generate)
    response = await client.post(
        "/api/v1/chat", headers=auth_headers, json={"message": "你好", "conversation_id": "empty"}
    )
    assert response.status_code == 503
    snapshot = await state(await owner_id(db), "empty", mode)
    assert snapshot.next == (("agent",) if mode == "single" else ("general",))
    resumed = await client.post(
        f"/api/v1/chat/conversations/empty/resume?mode={mode}", headers=auth_headers
    )
    assert resumed.status_code == 200 and resumed.json()["data"]["answer"]


@pytest.mark.parametrize("mode", ["single", "multi"])
async def test_checkpoint_uses_summary_and_uncovered_messages(
    client,
    auth_headers,
    db,
    memory_model,
    monkeypatch,
    mode,
):
    summary_tests.summaries.__wrapped__(monkeypatch)
    owner = await owner_id(db)
    await summary_tests.seed(owner, "long", stop=10, check=True)
    monkeypatch.setattr(settings, "AGENT_MODE", mode)
    await ask(client, "/api/v1/chat", "继续", "long", auth_headers)
    snapshot = await state(owner, "long", mode)
    if mode == "single":
        context = [message.content for message in snapshot.values["messages"][:-2]]
    else:
        context = [message["content"] for message in snapshot.values["history"]]
    assert len(context) == 11
    assert "摘要第1版" in context[0]
    assert "第6轮用户原话" in context[1]
    assert "第10轮助手建议" in context[-1]
    assert "第1轮用户原话" not in str(context)


async def test_deleting_user_cascades_native_checkpoint_storage(
    client,
    auth_headers,
    db,
    memory_model,
):
    await ask(client, "/api/v1/chat", "你好", "user-delete", auth_headers)
    owner = await db.get(User, await owner_id(db))
    await db.delete(owner)
    await db.commit()
    for name in (
        "agent_checkpoint_threads",
        "checkpoints",
        "checkpoint_writes",
        "checkpoint_blobs",
    ):
        assert await db.scalar(text(f"SELECT count(*) FROM {name}")) == 0


async def test_delete_unfinished_first_turn_reports_zero_messages(
    client,
    auth_headers,
    db,
    memory_model,
    monkeypatch,
):
    async def fail(current):
        raise RuntimeError("injected failure")

    monkeypatch.setattr(settings, "AGENT_MODE", "single")
    monkeypatch.setattr(agent, "_agent_node", fail)
    response = await client.post(
        "/api/v1/chat",
        headers=auth_headers,
        json={"message": "你好", "conversation_id": "unfinished-delete"},
    )
    assert response.status_code == 503
    deleted = await client.delete(
        "/api/v1/chat/conversations/unfinished-delete", headers=auth_headers
    )
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"deleted": 0, "deleted_checkpoints": 1}
    assert await db.scalar(select(func.count()).select_from(AgentCheckpointThread)) == 0
