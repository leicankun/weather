"""Opt-in live API/model acceptance; creates and finally deletes synthetic users.

Run in backend: python -m app.scripts.verify_checkpoint_memory --output ../docs/report.json
Existing reports are never overwritten. Uses the configured paid models.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.checkpoint import AgentCheckpointThread
from app.models.memory import MemoryCard
from app.models.user import User
from app.services import agent, chat_history_service, checkpoint_service, multi_agent


async def run(output: Path) -> bool:
    if output.exists():
        raise FileExistsError("Report already exists; choose a new --output path")
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "models": {
            "llm_provider": settings.LLM_PROVIDER,
            "embedding": settings.ZHIPU_EMBED_MODEL,
            "dimensions": settings.EMBED_DIM,
            "reranker": settings.RERANK_MODEL,
        },
        "scope": "Real models and PostgreSQL via ASGI API; saver/graph recreated, no forced process kill",
        "cases": [],
        "checks": [],
    }
    old = (settings.AGENT_MODE, settings.CHECKPOINT_ENABLED, settings.MEMORY_ENABLED)
    settings.CHECKPOINT_ENABLED = settings.MEMORY_ENABLED = True
    owners = []
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://live.test", timeout=100
        ) as client:
            for mode in ("single", "multi"):
                settings.AGENT_MODE = mode
                suffix = uuid.uuid4().hex[:12]
                async with AsyncSessionLocal() as db:
                    owner = User(username=f"cp_live_{suffix}", password_hash="unused-live-test")
                    db.add(owner)
                    await db.commit()
                    owners.append(owner.id)
                headers = {"Authorization": f"Bearer {create_access_token(owner.id)}"}
                cards_conv, recall_conv = f"cp-cards-{suffix}", f"cp-recall-{suffix}"
                code = "青鹭5816" if mode == "single" else "紫鸢9632"
                await chat_history_service.add_message(
                    owner.id, f"cp-old-{suffix}", "user", f"那次活动集合暗号是{code}。"
                )
                for i in range(12):
                    await chat_history_service.add_message(
                        owner.id, f"cp-old-{suffix}", "assistant", f"无关闲聊{i}"
                    )
                requests = [
                    (cards_conv, "我长期住在天河区，我不吃辣。请简短确认。", False),
                    (recall_conv, "我之前那次活动的集合暗号是什么？顺便提醒我的饮食偏好。", False),
                    (recall_conv, "再简短说一遍刚才提到的饮食偏好和暗号。", True),
                    (recall_conv, "我住哪个区？请只用已有信息回答。", False),
                ]
                for index, (conversation, question, stream) in enumerate(requests, 1):
                    started = time.perf_counter()
                    async with asyncio.timeout(100):
                        response = await client.post(
                            "/api/v1/chat/stream" if stream else "/api/v1/chat",
                            headers=headers,
                            json={"message": question, "conversation_id": conversation},
                        )
                    events = []
                    if stream:
                        events = [
                            json.loads(line[6:])
                            for line in response.text.splitlines()
                            if line.startswith("data: ")
                        ]
                        answer = "".join(
                            e.get("content", "") for e in events if e["type"] == "token"
                        )
                        passed = response.status_code == 200 and events[-1]["type"] == "done"
                    else:
                        body = response.json()
                        answer = body.get("data", {}).get("answer", "")
                        passed = response.status_code == 200 and body.get("code") == 0
                    report["cases"].append(
                        {
                            "mode": mode,
                            "question": question,
                            "stream": stream,
                            "answer": answer,
                            "http_status": response.status_code,
                            "completed": passed,
                            "seconds": round(time.perf_counter() - started, 2),
                        }
                    )
                    if not passed:
                        raise RuntimeError(f"{mode} case {index} did not complete")
                    if index == 2:
                        async with checkpoint_service.open_thread(
                            owner.id, conversation, mode, resume=True
                        ) as ctx:
                            graph = await (
                                multi_agent._build_graph(ctx.saver)
                                if mode == "multi"
                                else agent._build_graph(ctx.saver)
                            )
                            snapshot = await graph.aget_state(ctx.config)
                            background = snapshot.values.get("memory_context", "")
                        async with AsyncSessionLocal() as db:
                            cards = (
                                await db.scalars(
                                    select(MemoryCard).where(MemoryCard.user_id == owner.id)
                                )
                            ).all()
                            cards_text = json.dumps([c.data for c in cards], ensure_ascii=False)
                        report["checks"].extend(
                            [
                                {
                                    "mode": mode,
                                    "check": "cards and historical RAG in saved state",
                                    "passed": code in background and "辣" in background,
                                },
                                {
                                    "mode": mode,
                                    "check": "secret recalled from history, not extracted into card",
                                    "passed": code not in cards_text and code in answer,
                                },
                            ]
                        )
                before = len(
                    await chat_history_service.get_conversation_messages(owner.id, recall_conv)
                )
                for _ in range(2):
                    resumed = await client.post(
                        f"/api/v1/chat/conversations/{recall_conv}/resume?mode={mode}",
                        headers=headers,
                    )
                    report["checks"].append(
                        {
                            "mode": mode,
                            "check": "fresh graph/saver restores completed state",
                            "passed": resumed.status_code == 200
                            and resumed.json().get("data", {}).get("answer") == answer,
                        }
                    )
                after = len(
                    await chat_history_service.get_conversation_messages(owner.id, recall_conv)
                )
                report["checks"].append(
                    {
                        "mode": mode,
                        "check": "repeated resume does not duplicate messages",
                        "passed": before == after == 6,
                    }
                )
                status = await client.get(
                    f"/api/v1/chat/conversations/{recall_conv}/checkpoint?mode={mode}",
                    headers=headers,
                )
                report["checks"].append(
                    {
                        "mode": mode,
                        "check": "snapshot status saved",
                        "passed": status.json().get("data", {}).get("status") == "saved",
                    }
                )
                erased = await client.delete(
                    f"/api/v1/chat/conversations/{recall_conv}", headers=headers
                )
                async with AsyncSessionLocal() as db:
                    remaining = await db.scalar(
                        select(func.count())
                        .select_from(AgentCheckpointThread)
                        .where(
                            AgentCheckpointThread.user_id == owner.id,
                            AgentCheckpointThread.conversation_id == recall_conv,
                        )
                    )
                report["checks"].append(
                    {
                        "mode": mode,
                        "check": "delete conversation cleans thread",
                        "passed": erased.status_code == 200 and remaining == 0,
                    }
                )
    except Exception as exc:  # noqa: BLE001 写入实际故障报告并确保清理临时账号
        # Keep credentials/connection strings out of reports and logs.
        report["error_type"] = type(exc).__name__
    finally:
        settings.AGENT_MODE, settings.CHECKPOINT_ENABLED, settings.MEMORY_ENABLED = old
        async with AsyncSessionLocal() as db:
            if owners:
                await db.execute(delete(User).where(User.id.in_(owners)))
                await db.commit()
            report["cleanup_passed"] = not await db.scalar(
                select(func.count()).select_from(User).where(User.id.in_(owners))
            )
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["passed"] = (
            len(report["cases"]) == 8
            and all(c["completed"] for c in report["cases"])
            and all(c["passed"] for c in report["checks"])
            and "error_type" not in report
            and report["cleanup_passed"]
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as file:
            json.dump(report, file, ensure_ascii=False, indent=2)
        print(
            json.dumps(
                {
                    "passed": report["passed"],
                    "questions": len(report["cases"]),
                    "checks": len(report["checks"]),
                    "cleanup_passed": report["cleanup_passed"],
                }
            )
        )
    return report["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(run(args.output)) else 1)
