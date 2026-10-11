"""Real graph interrupts + PostgreSQL, with controllable model proposals."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.security import create_access_token
from app.models.agent_approval import AgentApproval
from app.models.chat_message import ChatMessage
from app.models.memory import MemoryCard, MemoryEpisode, MemoryState
from app.models.user import User
from app.schemas.agent_approval import ActionProposal, ProposedAction
from app.services import (
    agent,
    approval_service,
    chat_history_service,
    checkpoint_service,
    multi_agent,
)


@pytest.fixture
def workflow(monkeypatch):
    monkeypatch.setattr(settings, "CHECKPOINT_ENABLED", True)
    monkeypatch.setattr(settings, "AGENT_MODE", "single")
    agent._agent_instance = None
    multi_agent.reset_cache()
    data = {
        "actions": [ProposedAction(operation="delete_conversation", target_conversation_id="old")],
        "calls": 0,
    }

    async def propose(*args, **kwargs):
        data["calls"] += 1
        return ActionProposal(actions=data["actions"])

    async def tools():
        return []

    monkeypatch.setattr(approval_service, "ainvoke_json", propose)
    monkeypatch.setattr(agent, "_get_all_tools", tools)
    yield data
    agent._agent_instance = None
    multi_agent.reset_cache()


async def seed(db):
    owner = await db.scalar(select(User.id).where(User.username == "tester"))
    question, _ = await chat_history_service.add_turn(owner, "old", "合成旧会话", "旧答案")
    db.add(
        MemoryCard(
            user_id=owner,
            kind="preference",
            key="diet",
            data={"value": "不吃辣"},
            source_message_id=question,
        )
    )
    db.add(
        MemoryEpisode(
            user_id=owner,
            message_id=question,
            conversation_id="old",
            title="旧",
            source="test",
            content="不吃辣",
            chunk_index=0,
        )
    )
    await db.commit()
    return owner


async def request(client, headers, stream=False):
    response = await client.post(
        "/api/v1/chat/stream" if stream else "/api/v1/chat",
        headers=headers,
        json={"message": "请删除旧会话", "conversation_id": "approval-chat"},
    )
    assert response.status_code == 200
    if stream:
        events = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        assert events[-1]["type"] == "approval" and not any(e["type"] == "done" for e in events)
        return events[-1]
    assert response.json()["data"]["status"] == "waiting_approval"
    return response.json()["data"]["approval"]


async def decision(client, headers, payload, approved):
    return await client.post(
        f"/api/v1/chat/conversations/approval-chat/approval?mode={payload['mode']}",
        headers=headers,
        json={"approval_id": payload["approval_id"], "approved": approved},
    )


@pytest.mark.parametrize("mode", ["single", "multi"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("approved", [False, True])
async def test_pause_release_decide_resume_and_receipt(
    client,
    auth_headers,
    db,
    workflow,
    monkeypatch,
    mode,
    stream,
    approved,
):
    monkeypatch.setattr(settings, "AGENT_MODE", mode)
    owner = await seed(db)
    payload = await request(client, auth_headers, stream)
    assert payload["actions"][0]["counts"]["messages"] == 2
    assert payload["actions"][0]["counts"]["cards"] == 1
    assert "fingerprint" not in payload["actions"][0]
    assert len(await chat_history_service.get_recent_history(owner, "old")) == 2
    assert await db.scalar(select(func.count()).select_from(ChatMessage)) == 2
    # A separate request can acquire the same conversation lock: waiting doesn't hold resources.
    status = await client.get(
        f"/api/v1/chat/conversations/approval-chat/checkpoint?mode={mode}", headers=auth_headers
    )
    assert status.json()["data"]["status"] == "waiting_approval"
    resumed = await client.post(
        f"/api/v1/chat/conversations/approval-chat/resume?mode={mode}", headers=auth_headers
    )
    assert resumed.json()["data"]["status"] == "waiting_approval"
    assert workflow["calls"] == 1
    for _ in range(2):
        response = await decision(client, auth_headers, payload, approved)
        assert response.status_code == 200, response.text
    assert len(await chat_history_service.get_recent_history(owner, "old")) == (
        0 if approved else 2
    )
    assert len(await chat_history_service.get_recent_history(owner, "approval-chat")) == 2
    await db.refresh(await db.get(AgentApproval, payload["approval_id"]))
    row = await db.get(AgentApproval, payload["approval_id"])
    assert row.status == ("executed" if approved else "rejected")
    assert (row.executed_at is not None) == approved
    assert workflow["calls"] == 1


async def test_foreign_user_target_decision_id_and_parameter_tampering(
    client,
    auth_headers,
    db,
    workflow,
):
    await seed(db)
    other = User(username="approval_other", password_hash="unused")
    db.add(other)
    await db.commit()
    other_headers = {"Authorization": f"Bearer {create_access_token(other.id)}"}
    payload = await request(client, auth_headers)
    assert (
        await client.get(
            "/api/v1/chat/conversations/approval-chat/approvals", headers=other_headers
        )
    ).json()["data"]["items"] == []
    assert (await decision(client, other_headers, payload, True)).status_code == 404
    response = await client.post(
        "/api/v1/chat/conversations/approval-chat/approval",
        headers=auth_headers,
        json={
            "approval_id": payload["approval_id"],
            "approved": True,
            "target_conversation_id": "other",
        },
    )
    assert response.status_code == 422
    for value in ("true", 1, None):
        assert (await decision(client, auth_headers, payload, value)).status_code == 422
    wrong = {**payload, "approval_id": "0" * 32}
    assert (await decision(client, auth_headers, wrong, True)).status_code == 404
    workflow["actions"] = [
        ProposedAction(operation="delete_conversation", target_conversation_id="foreign")
    ]
    await chat_history_service.add_turn(other.id, "foreign", "他人", "不能删除")
    response = await client.post(
        "/api/v1/chat",
        headers=auth_headers,
        json={"message": "删除旧会话", "conversation_id": "another"},
    )
    assert "approval" not in response.json()["data"]
    assert len(await chat_history_service.get_recent_history(other.id, "foreign")) == 2


async def test_scope_change_invalidates_approval_instead_of_deleting_new_data(
    client,
    auth_headers,
    db,
    workflow,
):
    owner = await seed(db)
    payload = await request(client, auth_headers)
    await chat_history_service.add_turn(owner, "old", "审批后新消息", "新答案")
    response = await decision(client, auth_headers, payload, True)
    assert response.status_code == 200
    assert "范围已变化" in response.json()["data"]["answer"]
    assert len(await chat_history_service.get_recent_history(owner, "old")) == 4
    assert (await db.get(AgentApproval, payload["approval_id"])).status == "failed"


async def test_expiry_blocks_approve_but_allows_reject(client, auth_headers, db, workflow):
    await seed(db)
    payload = await request(client, auth_headers)
    row = await db.get(AgentApproval, payload["approval_id"])
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()
    assert (await decision(client, auth_headers, payload, True)).status_code == 409
    assert (await decision(client, auth_headers, payload, False)).status_code == 200


async def test_clear_memory_preserves_chat_sets_watermark_and_skips_reextract(
    client,
    auth_headers,
    db,
    workflow,
    monkeypatch,
):
    owner = await seed(db)
    workflow["actions"] = [ProposedAction(operation="clear_memory")]
    response = await client.post(
        "/api/v1/chat",
        headers=auth_headers,
        json={"message": "清空我的长期记忆", "conversation_id": "approval-chat"},
    )
    payload = response.json()["data"]["approval"]
    assert await db.scalar(select(func.count()).select_from(MemoryCard)) == 1
    response = await decision(client, auth_headers, payload, True)
    assert response.status_code == 200
    assert await db.scalar(select(func.count()).select_from(MemoryCard)) == 0
    assert await db.scalar(select(func.count()).select_from(MemoryEpisode)) == 0
    assert (await db.get(MemoryState, owner)).forget_before_id == 2
    assert len(await chat_history_service.get_recent_history(owner, "old")) == 2


async def test_recovery_after_effect_committed_before_graph_checkpoint_is_idempotent(
    client,
    auth_headers,
    db,
    workflow,
    monkeypatch,
):
    owner = await seed(db)
    payload = await request(client, auth_headers)
    original = approval_service.execute_node
    fail = True

    async def execute(state):
        nonlocal fail
        result = await original(state)
        if fail:
            fail = False
            raise RuntimeError("injected crash after atomic receipt")
        return result

    monkeypatch.setattr(approval_service, "execute_node", execute)
    response = await decision(client, auth_headers, payload, True)
    assert response.status_code == 503
    assert len(await chat_history_service.get_recent_history(owner, "old")) == 0
    # Recreate a conversation with the same ID: replay must not delete it.
    await chat_history_service.add_turn(owner, "old", "后来新建的会话", "保留")
    async with checkpoint_service.conversation_lock(owner, "old"):
        response = await client.post(
            "/api/v1/chat/conversations/approval-chat/resume", headers=auth_headers
        )
    assert response.status_code == 200, response.text
    assert len(await chat_history_service.get_recent_history(owner, "old")) == 2
    assert workflow["calls"] == 1


async def test_concurrent_decisions_and_active_target_guard(client, auth_headers, db, workflow):
    owner = await seed(db)
    payload = await request(client, auth_headers)
    async with checkpoint_service.conversation_lock(owner, "old"):
        assert (await decision(client, auth_headers, payload, True)).status_code == 409
    results = await asyncio.gather(
        *[decision(client, auth_headers, payload, True) for _ in range(2)]
    )
    assert all(r.status_code in (200, 409) for r in results)
    assert any(r.status_code == 200 for r in results)
    assert len(await chat_history_service.get_recent_history(owner, "approval-chat")) == 2
    assert (await decision(client, auth_headers, payload, False)).status_code == 409


async def test_reset_cancels_approval_and_pending_session_survives_refresh(
    client,
    auth_headers,
    db,
    workflow,
):
    await seed(db)
    payload = await request(client, auth_headers)
    items = (await client.get("/api/v1/chat/conversations", headers=auth_headers)).json()["data"][
        "items"
    ]
    assert any(i["conversation_id"] == "approval-chat" and i["pending"] for i in items)
    approvals = (
        await client.get("/api/v1/chat/conversations/approval-chat/approvals", headers=auth_headers)
    ).json()["data"]["items"]
    assert approvals[0]["approval_id"] == payload["approval_id"]
    await client.delete("/api/v1/chat/conversations/approval-chat/checkpoint", headers=auth_headers)
    row = await db.get(AgentApproval, payload["approval_id"])
    assert row.status == "cancelled" and row.thread_id is None
    assert (await decision(client, auth_headers, payload, True)).status_code == 404


async def test_no_login_no_checkpointer_no_raw_database_operation(
    client,
    auth_headers,
    db,
    workflow,
    monkeypatch,
):
    await seed(db)
    response = await client.post("/api/v1/chat", json={"message": "删除旧会话"})
    assert "approval" not in response.json()["data"]
    monkeypatch.setattr(settings, "CHECKPOINT_ENABLED", False)
    response = await client.post(
        "/api/v1/chat", headers=auth_headers, json={"message": "删除旧会话"}
    )
    assert "approval" not in response.json()["data"]
    monkeypatch.setattr(settings, "CHECKPOINT_ENABLED", True)
    response = await client.post(
        "/api/v1/chat", headers=auth_headers, json={"message": "删除整个数据库"}
    )
    assert "不支持" in response.json()["data"]["answer"]
    assert workflow["calls"] == 0


async def test_durable_decision_resumes_after_crash_before_command(
    client,
    auth_headers,
    db,
    workflow,
    monkeypatch,
):
    await seed(db)
    payload = await request(client, auth_headers)
    original = agent.chat

    async def crash(*args, **kwargs):
        raise RuntimeError("crash before Command resume")

    monkeypatch.setattr(agent, "chat", crash)
    assert (await decision(client, auth_headers, payload, True)).status_code == 503
    monkeypatch.setattr(agent, "chat", original)
    response = await client.post(
        "/api/v1/chat/conversations/approval-chat/resume", headers=auth_headers
    )
    assert response.status_code == 200, response.text
    assert (await db.get(AgentApproval, payload["approval_id"])).status == "executed"


async def test_rejection_does_not_wait_for_active_target(client, auth_headers, db, workflow):
    owner = await seed(db)
    payload = await request(client, auth_headers)
    async with checkpoint_service.conversation_lock(owner, "old"):
        response = await decision(client, auth_headers, payload, False)
    assert response.status_code == 200, response.text
    assert (await db.get(AgentApproval, payload["approval_id"])).status == "rejected"
    assert len(await chat_history_service.get_recent_history(owner, "old")) == 2


async def test_clear_rechecks_unindexed_chat_watermark(client, auth_headers, db, workflow):
    owner = await seed(db)
    workflow["actions"] = [ProposedAction(operation="clear_memory")]
    response = await client.post(
        "/api/v1/chat",
        headers=auth_headers,
        json={"message": "清空长期记忆", "conversation_id": "approval-chat"},
    )
    payload = response.json()["data"]["approval"]
    await chat_history_service.add_turn(owner, "new", "审批期间的新消息", "还没有索引")
    response = await decision(client, auth_headers, payload, True)
    assert response.status_code == 200
    assert "范围已变化" in response.json()["data"]["answer"]
    assert await db.scalar(select(func.count()).select_from(MemoryCard)) == 1
    assert await db.scalar(select(func.count()).select_from(MemoryEpisode)) == 1
    assert await db.get(MemoryState, owner) is None


async def test_clear_does_not_expand_watermark_after_validation(
    client, auth_headers, db, workflow, monkeypatch
):
    owner = await seed(db)
    workflow["actions"] = [ProposedAction(operation="clear_memory")]
    response = await client.post(
        "/api/v1/chat",
        headers=auth_headers,
        json={"message": "清空长期记忆", "conversation_id": "approval-chat"},
    )
    payload = response.json()["data"]["approval"]
    original = approval_service.memory_service.clear_in_transaction

    async def write_during_clear(*args, **kwargs):
        await chat_history_service.add_turn(owner, "new", "执行期间的新消息", "需要后续索引")
        return await original(*args, **kwargs)

    monkeypatch.setattr(approval_service.memory_service, "clear_in_transaction", write_during_clear)
    response = await decision(client, auth_headers, payload, True)
    assert response.status_code == 200, response.text
    assert (await db.get(MemoryState, owner)).forget_before_id == 2
    assert len(await chat_history_service.get_recent_history(owner, "new")) == 2
    assert await db.scalar(select(func.count()).select_from(MemoryCard)) == 0


@pytest.mark.parametrize("crash", [False, True])
async def test_batch_atomicity_and_actual_clear_receipt(
    client, auth_headers, db, workflow, monkeypatch, crash
):
    owner = await seed(db)
    workflow["actions"] = [
        ProposedAction(operation="delete_conversation", target_conversation_id="old"),
        ProposedAction(operation="clear_memory"),
    ]
    payload = await request(client, auth_headers)
    assert payload["actions"][1]["counts"]["cards"] == 1
    original = approval_service.memory_service.clear_in_transaction

    async def fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("injected transaction failure")

    if crash:
        monkeypatch.setattr(approval_service.memory_service, "clear_in_transaction", fail)
    response = await decision(client, auth_headers, payload, True)
    if crash:
        assert response.status_code == 503
        assert len(await chat_history_service.get_recent_history(owner, "old")) == 2
        assert await db.scalar(select(func.count()).select_from(MemoryCard)) == 1
        assert await db.scalar(select(func.count()).select_from(MemoryEpisode)) == 1
        assert await db.get(MemoryState, owner) is None
        monkeypatch.setattr(approval_service.memory_service, "clear_in_transaction", original)
        response = await decision(client, auth_headers, payload, True)
    assert response.status_code == 200, response.text
    row = await db.get(AgentApproval, payload["approval_id"])
    await db.refresh(row)
    assert row.status == "executed"
    assert row.result["receipts"][1]["counts"] == {"cards": 0, "history_chunks": 0}
    assert len(await chat_history_service.get_recent_history(owner, "old")) == 0
    assert await db.scalar(select(func.count()).select_from(MemoryCard)) == 0
