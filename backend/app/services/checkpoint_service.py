"""Authenticated graph persistence, conversation locking and restart recovery.

SQL/serialization use the official PostgresSaver. Its synchronous driver runs in
worker threads: this also works with Windows' default Proactor event loop.
Connections are request-scoped and never shared across asyncio loops.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

import psycopg
from fastapi import HTTPException
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Overwrite
from psycopg.rows import dict_row
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.chat_message import ChatMessage
from app.models.checkpoint import AgentCheckpointThread

Mode = Literal["single", "multi"]


class ThreadedPostgresSaver(PostgresSaver):
    """Async scheduling bridge; checkpoint semantics remain the official implementation."""

    async def aget_tuple(self, config):
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        items = await asyncio.to_thread(
            lambda: list(self.list(config, filter=filter, before=before, limit=limit))
        )
        for item in items:
            yield item

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id):
        await asyncio.to_thread(self.delete_thread, thread_id)


@dataclass
class ThreadContext:
    saver: ThreadedPostgresSaver
    config: dict
    user_id: int
    conversation_id: str
    mode: Mode
    request: dict
    finalized: bool
    resume: bool = False


_current: ContextVar[ThreadContext | None] = ContextVar("agent_checkpoint", default=None)


def current() -> ThreadContext | None:
    return _current.get()


def thread_id(user_id: int, conversation_id: str, mode: Mode) -> str:
    # UUID avoids ambiguous separators; identifiers are always derived on the server.
    return uuid.uuid5(uuid.NAMESPACE_URL, f"weather:{user_id}:{conversation_id}:{mode}").hex


@asynccontextmanager
async def conversation_lock(user_id: int | None, conversation_id: str):
    """Cross-process guard covering history reads, model work, final save and deletion."""
    if user_id is None or not settings.CHECKPOINT_ENABLED:
        yield None
        return
    url = make_url(settings.DB_URL).set(drivername="postgresql")
    conn = await asyncio.to_thread(
        psycopg.connect,
        url.render_as_string(hide_password=False),
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
        connect_timeout=10,
    )
    try:
        cursor = await asyncio.to_thread(
            conn.execute,
            "SELECT pg_try_advisory_lock(7318, hashtext(%s)) AS locked",
            (f"{user_id}:{conversation_id}",),
        )
        locked = await asyncio.to_thread(cursor.fetchone)
        if not locked["locked"]:
            raise HTTPException(409, "此会话正在处理请求，请稍后重试")
        yield conn
    finally:
        await asyncio.to_thread(conn.close)  # connection close releases session advisory locks


@asynccontextmanager
async def open_thread(
    user_id: int | None,
    conversation_id: str,
    mode: Mode,
    *,
    request: dict | None = None,
    resume: bool = False,
    enabled: bool = True,
):
    """One HTTP invocation owns one saver. Resume never submits a new user message."""
    async with conversation_lock(user_id, conversation_id) as conn:
        if conn is None or not enabled:
            if resume:
                raise HTTPException(409, "Checkpointer 未启用")
            yield None
            return
        identifier = thread_id(user_id, conversation_id, mode)
        saver = ThreadedPostgresSaver(conn)
        config = {"configurable": {"thread_id": identifier}}
        previous = await saver.aget_tuple(config)
        async with AsyncSessionLocal() as db:
            row = await db.get(AgentCheckpointThread, identifier)
            if resume:
                if row is None or previous is None:
                    raise HTTPException(404, "此会话没有可恢复的图状态")
                if previous.checkpoint["channel_values"].get("run_id") != row.request.get("run_id"):
                    raise HTTPException(409, "本轮尚未启动图，请清除检查点后重新发送")
            else:
                # Single/multi have different graph states but share one chat timeline.
                pending = await db.scalars(
                    select(AgentCheckpointThread).where(
                        AgentCheckpointThread.user_id == user_id,
                        AgentCheckpointThread.conversation_id == conversation_id,
                        AgentCheckpointThread.assistant_message_id.is_(None),
                    )
                )
                for unfinished in pending:
                    saved = await saver.aget_tuple(
                        {"configurable": {"thread_id": unfinished.thread_id}}
                    )
                    if saved and saved.checkpoint["channel_values"].get(
                        "run_id"
                    ) == unfinished.request.get("run_id"):
                        raise HTTPException(
                            409, f"{unfinished.mode} 上轮尚未保存完成，请恢复或清除检查点后再发消息"
                        )
                if row is None:
                    row = AgentCheckpointThread(
                        thread_id=identifier,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        mode=mode,
                    )
                    db.add(row)
                row.request = {**(request or {}), "run_id": uuid.uuid4().hex}
                row.assistant_message_id = None
                await db.commit()
            ctx = ThreadContext(
                saver,
                config,
                user_id,
                conversation_id,
                mode,
                dict(row.request),
                row.assistant_message_id is not None,
                resume,
            )
        token = _current.set(ctx)
        try:
            yield ctx
        finally:
            _current.reset(token)


async def graph_and_input(builder, cached_getter, initial: dict, reducer_keys: tuple[str, ...]):
    ctx = current()
    if ctx is None:
        return await cached_getter(), initial, {}
    graph = await builder(checkpointer=ctx.saver)
    if ctx.resume:
        return graph, None, {"config": ctx.config, "durability": "sync"}
    # Refresh bounded short-term context + cards/RAG. Keep prior snapshots in storage,
    # but replace reducer fields so tool messages/domain results do not accumulate.
    initial = {
        **initial,
        "run_id": ctx.request["run_id"],
        "approval_id": "",
        "action_approved": False,
        "action_result": {},
    }
    for key in reducer_keys:
        initial[key] = Overwrite(initial[key])
    return graph, initial, {"config": ctx.config, "durability": "sync"}


async def invoke(graph, initial, kwargs):
    ctx = current()
    if ctx and ctx.resume:
        snapshot = await graph.aget_state(ctx.config)
        from app.services import approval_service

        resumed = await approval_service.resume_input(snapshot)
        if isinstance(resumed, dict) and resumed.get("waiting"):
            return {**snapshot.values, "__interrupt__": resumed["interrupts"]}
        if resumed is not None:
            return await graph.ainvoke(resumed, **kwargs)
        if not snapshot.next:
            return snapshot.values
    return await graph.ainvoke(initial, **kwargs)


async def save_turn(stored_message: str, answer: str) -> tuple[int, int] | None:
    """Commit messages and completion marker atomically; replay is idempotent."""
    ctx = current()
    if ctx is None:
        raise RuntimeError("save_turn requires a checkpoint context")
    async with AsyncSessionLocal() as db:
        row = await db.get(AgentCheckpointThread, ctx.config["configurable"]["thread_id"])
        if row.assistant_message_id is not None:
            return None
        question = ChatMessage(
            user_id=ctx.user_id,
            conversation_id=ctx.conversation_id,
            role="user",
            content=stored_message,
        )
        response = ChatMessage(
            user_id=ctx.user_id,
            conversation_id=ctx.conversation_id,
            role="assistant",
            content=answer,
        )
        db.add_all([question, response])
        await db.flush()
        row.assistant_message_id = response.id
        await db.commit()
        ctx.finalized = True
        return question.id, response.id


async def describe(user_id: int, conversation_id: str, mode: Mode) -> dict:
    async with conversation_lock(user_id, conversation_id) as conn:
        if conn is None:
            raise HTTPException(409, "Checkpointer 未启用")
        identifier = thread_id(user_id, conversation_id, mode)
        saver = ThreadedPostgresSaver(conn)
        config = {"configurable": {"thread_id": identifier}}
        async with AsyncSessionLocal() as db:
            row = await db.get(AgentCheckpointThread, identifier)
            if row is None:
                raise HTTPException(404, "检查点不存在")
            latest = await saver.aget_tuple(config)
            if latest is None or latest.checkpoint["channel_values"].get(
                "run_id"
            ) != row.request.get("run_id"):
                return {"mode": mode, "status": "not_started", "snapshots": []}
            # Only metadata is exposed; no tools, prompts, private documents or raw state.
            history = []
            async for item in saver.alist(config, limit=20):
                history.append(
                    {
                        "checkpoint_id": item.config["configurable"]["checkpoint_id"],
                        "step": item.metadata.get("step"),
                        "created_at": item.checkpoint.get("ts"),
                    }
                )
            return {
                "conversation_id": conversation_id,
                "mode": mode,
                "status": "saved"
                if row.assistant_message_id
                else (
                    "waiting_approval"
                    if any(
                        channel == "__interrupt__"
                        for _, channel, _ in (latest.pending_writes or [])
                    )
                    else "unfinished"
                ),
                "snapshots": history,
            }


async def reset(user_id: int, conversation_id: str, mode: Mode) -> int:
    async with conversation_lock(user_id, conversation_id), AsyncSessionLocal() as db:
        from app.services import approval_service

        await approval_service.cancel_for_threads(db, [thread_id(user_id, conversation_id, mode)])
        result = await db.execute(
            delete(AgentCheckpointThread).where(
                AgentCheckpointThread.user_id == user_id,
                AgentCheckpointThread.conversation_id == conversation_id,
                AgentCheckpointThread.mode == mode,
            )
        )
        await db.commit()
        return result.rowcount


async def delete_threads(db, user_id: int, conversation_id: str) -> int:
    from app.services import approval_service

    identifiers = list(
        await db.scalars(
            select(AgentCheckpointThread.thread_id).where(
                AgentCheckpointThread.user_id == user_id,
                AgentCheckpointThread.conversation_id == conversation_id,
            )
        )
    )
    await approval_service.cancel_for_threads(db, identifiers)
    result = await db.execute(
        delete(AgentCheckpointThread).where(
            AgentCheckpointThread.user_id == user_id,
            AgentCheckpointThread.conversation_id == conversation_id,
        )
    )
    return result.rowcount
