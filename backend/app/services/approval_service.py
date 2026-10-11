"""Shared management workflow: propose -> interrupt -> authorize -> execute.

Dangerous parameters are frozen before interrupt. Decisions are server records,
never model text. Database changes and the execution receipt commit together.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from langgraph.types import interrupt
from sqlalchemy import func, select, update

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.agent_approval import AgentApproval
from app.models.chat_message import ChatMessage
from app.models.chat_summary import ChatSummary
from app.models.checkpoint import AgentCheckpointThread, checkpoints
from app.models.memory import MemoryCard, MemoryEpisode, MemoryState
from app.models.user import User
from app.schemas.agent_approval import ActionProposal
from app.services import chat_history_service, checkpoint_service, memory_service
from app.services.llm_client import ainvoke_json

POLICIES = {
    "delete_conversation": {"title": "删除本人会话", "requires_approval": True},
    "clear_memory": {"title": "清空本人长期记忆", "requires_approval": True},
}


def is_management_request(text: str) -> bool:
    # Route destructive requests to a dedicated workflow, never concurrent domain loops.
    return bool(
        re.search(
            r"(?:删除|删掉|清空|清除|忘掉|忘记|移除).*(?:会话|对话|聊天|记忆|数据库)|"
            r"(?:会话|对话|聊天|记忆|数据库).*(?:删除|删掉|清空|清除)|drop\s+(?:database|table)|删库",
            text,
            re.IGNORECASE,
        )
    )


async def _snapshot(db, user_id: int, operation: str, target: str | None) -> dict:
    watermarks = []
    if operation == "delete_conversation":
        rows = (
            await db.execute(
                select(ChatMessage.id, ChatMessage.content)
                .where(
                    ChatMessage.user_id == user_id,
                    ChatMessage.conversation_id == target,
                )
                .order_by(ChatMessage.id)
            )
        ).all()
        ids = [r.id for r in rows]
        summaries = (
            await db.execute(
                select(ChatSummary.version, ChatSummary.summary).where(
                    ChatSummary.user_id == user_id,
                    ChatSummary.conversation_id == target,
                )
            )
        ).all()
        threads = (
            (
                await db.execute(
                    select(AgentCheckpointThread.thread_id)
                    .where(
                        AgentCheckpointThread.user_id == user_id,
                        AgentCheckpointThread.conversation_id == target,
                    )
                    .order_by(AgentCheckpointThread.thread_id)
                )
            )
            .scalars()
            .all()
        )
        snapshots = (
            await db.scalars(
                select(checkpoints.c.checkpoint_id)
                .where(
                    checkpoints.c.thread_id.in_(threads),
                )
                .order_by(checkpoints.c.checkpoint_id)
            )
        ).all()
        card_filter = MemoryCard.source_message_id.in_(ids)
        episode_filter = MemoryEpisode.message_id.in_(ids)
    else:
        rows, summaries, threads, snapshots = [], [], [], []
        card_filter = MemoryCard.user_id == user_id
        episode_filter = MemoryEpisode.user_id == user_id
        # Clearing also advances the backfill watermark. Freeze its range so chats
        # written while waiting cannot silently become forgotten by this approval.
        watermarks = [
            await db.scalar(select(func.max(ChatMessage.id)).where(ChatMessage.user_id == user_id))
            or 0,
            await db.scalar(
                select(MemoryState.forget_before_id).where(MemoryState.user_id == user_id)
            )
            or 0,
        ]
    cards = (
        await db.execute(
            select(MemoryCard.id, MemoryCard.data)
            .where(
                MemoryCard.user_id == user_id,
                card_filter,
            )
            .order_by(MemoryCard.id)
        )
    ).all()
    episodes = (
        await db.execute(
            select(MemoryEpisode.id, MemoryEpisode.content)
            .where(
                MemoryEpisode.user_id == user_id,
                episode_filter,
            )
            .order_by(MemoryEpisode.id)
        )
    ).all()
    counts = {
        "messages": len(rows),
        "summaries": len(summaries),
        "threads": len(threads),
        "checkpoints": len(snapshots),
        "cards": len(cards),
        "history_chunks": len(episodes),
    }
    encoded = json.dumps(
        [list(r) for r in rows]
        + [list(r) for r in summaries]
        + list(threads)
        + list(snapshots)
        + [list(r) for r in cards]
        + [list(r) for r in episodes]
        + [watermarks],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return {
        "counts": counts,
        "fingerprint": hashlib.sha256(encoded.encode()).hexdigest(),
        **({"forget_before_id": max(watermarks)} if watermarks else {}),
    }


def public(row: AgentApproval) -> dict:
    return {
        "approval_id": row.id,
        "conversation_id": row.conversation_id,
        "mode": row.mode,
        "status": row.status,
        "expires_at": row.expires_at.isoformat(),
        "actions": [
            {k: v for k, v in a.items() if k not in {"fingerprint", "forget_before_id"}}
            for a in row.actions
        ],
    }


async def active(user_id: int, conversation_id: str, mode: str | None = None) -> list[dict]:
    async with AsyncSessionLocal() as db:
        stmt = (
            select(AgentApproval)
            .join(
                AgentCheckpointThread,
                AgentCheckpointThread.thread_id == AgentApproval.thread_id,
            )
            .where(
                AgentApproval.user_id == user_id,
                AgentApproval.conversation_id == conversation_id,
                AgentApproval.run_id == AgentCheckpointThread.request["run_id"].astext,
                AgentCheckpointThread.assistant_message_id.is_(None),
                AgentApproval.status != "cancelled",
            )
        )
        if mode:
            stmt = stmt.where(AgentApproval.mode == mode)
        rows = (await db.scalars(stmt)).all()
        items = []
        for row in rows:
            thread = await db.get(AgentCheckpointThread, row.thread_id)
            items.append({**public(row), "question": thread.request.get("stored_message", "")})
        return items


async def prepare_node(state: dict) -> dict:
    ctx = checkpoint_service.current()
    if ctx is None:
        return {"answer": "敏感操作需要登录并开启 Checkpointer 才能发起审批。"}
    identifier = ctx.config["configurable"]["thread_id"]
    async with AsyncSessionLocal() as db:
        existing = await db.scalar(
            select(AgentApproval).where(
                AgentApproval.thread_id == identifier,
                AgentApproval.run_id == state["run_id"],
            )
        )
        if existing:
            return {"approval_id": existing.id}
        conversations = await chat_history_service.list_conversations(ctx.user_id, db=db)
    question = state.get("question") or state["messages"][-1].content
    if re.search(
        r"drop\s+(database|table)|删库|(?:删除|清空|删掉)(?:整个|全部|所有)?数据库", question, re.I
    ):
        return {
            "answer": "不支持删除整个数据库或执行任意 SQL。可申请删除本人指定会话或清空本人长期记忆。"
        }
    plan = await ainvoke_json(
        json.dumps(
            {
                "question": question,
                "current_conversation_id": ctx.conversation_id,
                "owned_conversations": conversations,
            },
            ensure_ascii=False,
        ),
        ActionProposal,
        system=(
            "你是数据管理 Agent，只提出操作，不执行操作。允许 delete_conversation 删除本人指定会话；"
            "clear_memory 清空本人偏好事实卡片和历史 RAG，不删除聊天或短期摘要。"
            "每个操作稍后都须人类批准。只从 owned_conversations 选择明确的目标 ID；"
            "不能删除正在审批的 current_conversation_id。不明确、假设、询问能力或不在允许范围内的请求，"
            "返回 actions=[] 并 clarification 说明或询问。不要将删除数据库转成清空记忆，"
            "不要把‘删除全部聊天’擅自缩小为列表中的部分会话。clear_memory 的 target_conversation_id 为 null。"
        ),
    )
    if not plan.actions:
        return {"answer": plan.clarification or "请明确要删除哪个会话，或是否清空本人长期记忆。"}
    actions, seen = [], set()
    async with AsyncSessionLocal() as db:
        for proposal in plan.actions:
            operation, target = proposal.operation, proposal.target_conversation_id
            if (operation, target) in seen:
                continue
            seen.add((operation, target))
            if operation not in POLICIES or not POLICIES[operation]["requires_approval"]:
                raise HTTPException(400, "操作不在审批策略允许范围")
            if operation == "clear_memory" and target is not None:
                return {"answer": "清空长期记忆不能指定会话，请重新说明。"}
            if operation == "delete_conversation" and (not target or target == ctx.conversation_id):
                return {"answer": "请在新会话中指定要删除的旧会话，不能删除正在审批的会话。"}
            scope = await _snapshot(db, ctx.user_id, operation, target)
            if operation == "delete_conversation" and not (
                scope["counts"]["messages"] or scope["counts"]["threads"]
            ):
                return {"answer": "未找到本人名下的目标会话，没有执行删除。"}
            title = next(
                (c["title"] for c in conversations if c["conversation_id"] == target), target
            )
            actions.append(
                {
                    "operation": operation,
                    "title": POLICIES[operation]["title"],
                    "target_conversation_id": target,
                    "target_label": title or "本人全部长期记忆",
                    **scope,
                    "effect": (
                        "删除目标会话原始消息、短期摘要、图快照和以这些消息为来源的记忆，不影响其他会话。"
                        if operation == "delete_conversation"
                        else "清空本人全部偏好/事实卡片和历史 RAG，设置遗忘水位；原始聊天、短期摘要和图快照保留。"
                    ),
                }
            )
        row = AgentApproval(
            id=uuid.uuid4().hex,
            user_id=ctx.user_id,
            thread_id=identifier,
            conversation_id=ctx.conversation_id,
            mode=ctx.mode,
            run_id=state["run_id"],
            actions=actions,
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.AGENT_APPROVAL_TTL_SECONDS),
        )
        db.add(row)
        await db.commit()
        return {"approval_id": row.id}


def after_prepare(state: dict) -> str:
    return "approve_action" if state.get("approval_id") else "management_result"


async def approve_node(state: dict) -> dict:
    async with AsyncSessionLocal() as db:
        row = await db.get(AgentApproval, state["approval_id"])
        payload = public(row)
    # Intentionally its own node: no model calls or mutations before the interrupt.
    decision = interrupt(payload)
    ctx = checkpoint_service.current()
    async with AsyncSessionLocal() as db:
        row = await db.get(AgentApproval, state["approval_id"])
        if (
            not isinstance(decision, dict)
            or decision.get("approval_id") != row.id
            or row.user_id != ctx.user_id
            or row.run_id != ctx.request["run_id"]
            or row.status == "pending"
            or decision.get("approved") != (row.status in {"approved", "executed", "failed"})
        ):
            raise HTTPException(409, "审批结果未通过服务端校验")
    return {"action_approved": decision["approved"]}


async def decide(approval_id: str, approved: bool, graph) -> None:
    ctx = checkpoint_service.current()
    snapshot = await graph.aget_state(ctx.config)
    async with AsyncSessionLocal() as db:
        row = await db.get(AgentApproval, approval_id, with_for_update=True)
        if (
            row is None
            or row.user_id != ctx.user_id
            or row.thread_id != ctx.config["configurable"]["thread_id"]
            or row.run_id != ctx.request["run_id"]
        ):
            raise HTTPException(404, "审批不存在或不属于本次任务")
        if row.status == "pending":
            if not any(
                i.value.get("approval_id") == row.id
                for task in snapshot.tasks
                for i in task.interrupts
            ):
                raise HTTPException(409, "流程尚未暂停到审批节点，请先恢复流程")
            if approved and row.expires_at <= datetime.now(UTC):
                raise HTTPException(409, "审批已过期，请拒绝后重新发起")
            row.status = "approved" if approved else "rejected"
            row.decided_at = datetime.now(UTC)
            await db.commit()
        elif row.status not in ({"approved", "executed", "failed"} if approved else {"rejected"}):
            raise HTTPException(409, "审批已经处理，不能更改决定")


async def resume_input(snapshot):
    """Generic resume may replay a durable decision, but cannot manufacture consent."""
    pending = [i for task in snapshot.tasks for i in task.interrupts]
    if not pending:
        return None
    ctx = checkpoint_service.current()
    async with AsyncSessionLocal() as db:
        row = await db.get(AgentApproval, snapshot.values.get("approval_id"))
        if not row or row.user_id != ctx.user_id or row.status == "pending":
            return {"waiting": True, "interrupts": pending}
        if row.status not in {"approved", "executed", "failed", "rejected"}:
            raise HTTPException(409, "审批已取消，请清除图状态后重新发起")
        from langgraph.types import Command

        return Command(
            resume={
                i.id: {"approval_id": row.id, "approved": row.status != "rejected"} for i in pending
            }
        )


async def execute_node(state: dict) -> dict:
    ctx = checkpoint_service.current()
    # Lock all affected conversations before the audit/data transaction.
    async with AsyncSessionLocal() as db:
        row = await db.get(AgentApproval, state["approval_id"])
        if (
            row is None
            or row.user_id != ctx.user_id
            or row.run_id != ctx.request["run_id"]
            or row.thread_id != ctx.config["configurable"]["thread_id"]
        ):
            raise HTTPException(403, "审批归属不匹配")
        # Cancellation and replay of a receipt need no access to the former target.
        if row.status in {"executed", "failed"}:
            return {"action_result": row.result}
        if row.status == "rejected":
            return {"action_result": {"answer": "你已拒绝，本次操作已取消，没有删除数据。"}}
        targets = sorted(
            {
                a["target_conversation_id"]
                for a in row.actions
                if a["operation"] == "delete_conversation"
            }
        )
    async with AsyncExitStack() as stack:
        for target in targets:
            if target == ctx.conversation_id:
                raise HTTPException(409, "不能删除当前审批会话")
            await stack.enter_async_context(
                checkpoint_service.conversation_lock(ctx.user_id, target)
            )
        async with AsyncSessionLocal() as db:
            row = await db.get(AgentApproval, state["approval_id"], with_for_update=True)
            if (
                row.user_id != ctx.user_id
                or row.run_id != ctx.request["run_id"]
                or row.thread_id != ctx.config["configurable"]["thread_id"]
            ):
                raise HTTPException(403, "审批归属不匹配")
            if row.status in {"executed", "failed"}:
                return {"action_result": row.result}
            if row.status == "rejected":
                return {"action_result": {"answer": "你已拒绝，本次操作已取消，没有删除数据。"}}
            if row.status != "approved" or not state.get("action_approved"):
                raise HTTPException(409, "敏感操作未经批准")
            if not await db.get(User, ctx.user_id):
                raise HTTPException(403, "用户已失效")
            await memory_service._lock(db, ctx.user_id)
            for target in targets:
                await chat_history_service.lock_conversation(db, ctx.user_id, target)
            unchanged = True
            for action in row.actions:
                if action["operation"] not in POLICIES:
                    unchanged = False
                    break
                scope = await _snapshot(
                    db, ctx.user_id, action["operation"], action["target_conversation_id"]
                )
                if scope["fingerprint"] != action["fingerprint"]:
                    unchanged = False
                    break
            if not unchanged or row.expires_at <= datetime.now(UTC):
                row.status = "failed"
                row.result = {
                    "answer": "审批范围已变化或审批已过期，没有执行删除，请重新发起确认。"
                }
            else:
                receipts = []
                for action in row.actions:
                    if action["operation"] == "delete_conversation":
                        counts = await chat_history_service.delete_in_transaction(
                            db, ctx.user_id, action["target_conversation_id"]
                        )
                    else:
                        # Freeze the watermark even if another conversation writes
                        # new raw messages after validation but before this deletion.
                        counts = await memory_service.clear_in_transaction(
                            db, ctx.user_id, forget_before_id=action["forget_before_id"]
                        )
                    receipts.append(
                        {
                            "operation": action["operation"],
                            "target": action["target_label"],
                            "counts": counts,
                        }
                    )
                row.status = "executed"
                row.executed_at = datetime.now(UTC)
                row.result = {"answer": "已按你批准的范围完成操作。", "receipts": receipts}
            # Side effects and receipt are atomic; graph replay reads this receipt.
            await db.commit()
            return {"action_result": row.result}


def result_node(state: dict) -> dict:
    result = state.get("action_result") or {}
    answer = result.get("answer") or state.get("answer") or "没有执行数据操作。"
    for item in result.get("receipts", []):
        counts = item["counts"]
        detail = (
            f"删除 {counts['deleted']} 条聊天和 {counts['deleted_checkpoints']} 组执行记录"
            if item["operation"] == "delete_conversation"
            else f"清空 {counts['cards']} 张卡片和 {counts['history_chunks']} 条历史记忆片段"
        )
        answer += f"\n{POLICIES[item['operation']]['title']}：{item['target']}，{detail}。"
    return {"answer": answer}


def add_nodes(graph):
    graph.add_node("prepare_action", prepare_node)
    graph.add_node("approve_action", approve_node)
    graph.add_node("execute_action", execute_node)
    graph.add_node("management_result", result_node)
    graph.add_conditional_edges(
        "prepare_action", after_prepare, ["approve_action", "management_result"]
    )
    graph.add_edge("approve_action", "execute_action")
    graph.add_edge("execute_action", "management_result")
    from langgraph.graph import END

    graph.add_edge("management_result", END)


async def cancel_for_threads(db, identifiers: list[str]) -> None:
    await db.execute(
        update(AgentApproval)
        .where(
            AgentApproval.thread_id.in_(identifiers),
            AgentApproval.status.in_(["pending", "approved"]),
        )
        .values(status="cancelled")
    )
