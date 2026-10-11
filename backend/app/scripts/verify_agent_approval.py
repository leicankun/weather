"""Real LLM proposals + API interrupts/decisions, using synthetic users only."""

import argparse
import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.agent_approval import AgentApproval
from app.models.memory import MemoryCard, MemoryEpisode
from app.models.user import User
from app.services import chat_history_service


async def run(output: Path) -> bool:
    if output.exists():
        raise FileExistsError("Report exists; choose another --output")
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "llm_provider": settings.LLM_PROVIDER,
        "scope": "Real configured LLM, FastAPI ASGI, PostgreSQL; no embedding or reranker calls needed",
        "cases": [],
        "checks": [],
    }
    owners = []
    original = settings.AGENT_MODE, settings.CHECKPOINT_ENABLED
    settings.CHECKPOINT_ENABLED = True
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://live.test"
        ) as client:
            for mode in ("single", "multi"):
                settings.AGENT_MODE = mode
                suffix = uuid.uuid4().hex[:10]
                async with AsyncSessionLocal() as db:
                    user = User(username=f"approval_live_{suffix}", password_hash="unused")
                    db.add(user)
                    await db.commit()
                    owners.append(user.id)
                headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}
                target, conversation = f"live-target-{suffix}", f"live-approval-{suffix}"
                await chat_history_service.add_turn(user.id, target, "合成旧聊天记录", "合成回答")
                for approved in (False, True):
                    question = f"请删除会话 ID 为 {target} 的旧聊天记录。"
                    async with asyncio.timeout(90):
                        response = await client.post(
                            "/api/v1/chat",
                            headers=headers,
                            json={"message": question, "conversation_id": conversation},
                        )
                    data = response.json().get("data", {})
                    report["cases"].append(
                        {
                            "mode": mode,
                            "question": question,
                            "http_status": response.status_code,
                            "response": data,
                        }
                    )
                    payload = data["approval"]
                    assert data["status"] == "waiting_approval"
                    assert payload["actions"][0]["target_conversation_id"] == target
                    assert len(await chat_history_service.get_recent_history(user.id, target)) == 2
                    restored = await client.post(
                        f"/api/v1/chat/conversations/{conversation}/resume?mode={mode}",
                        headers=headers,
                    )
                    assert restored.json()["data"]["status"] == "waiting_approval"
                    for _ in range(2):
                        decided = await client.post(
                            f"/api/v1/chat/conversations/{conversation}/approval?mode={mode}",
                            headers=headers,
                            json={"approval_id": payload["approval_id"], "approved": approved},
                        )
                        assert decided.status_code == 200, decided.status_code
                    remaining = len(await chat_history_service.get_recent_history(user.id, target))
                    assert remaining == (0 if approved else 2)
                    report["checks"].append(
                        {
                            "mode": mode,
                            "check": "approve deletes only after consent"
                            if approved
                            else "reject preserves target",
                            "passed": True,
                            "final_answer": decided.json()["data"]["answer"],
                            "remaining_messages": remaining,
                        }
                    )
                source, _ = await chat_history_service.add_turn(
                    user.id, target, "我不吃辣", "合成偏好来源"
                )
                async with AsyncSessionLocal() as db:
                    db.add(
                        MemoryCard(
                            user_id=user.id,
                            kind="preference",
                            key="diet",
                            data={"value": "不吃辣"},
                            source_message_id=source,
                        )
                    )
                    db.add(
                        MemoryEpisode(
                            user_id=user.id,
                            message_id=source,
                            conversation_id=target,
                            title="合成",
                            source="test",
                            content="不吃辣",
                            chunk_index=0,
                        )
                    )
                    await db.commit()
                question = "请清空我的长期记忆卡片和历史记忆库，保留聊天记录。"
                async with asyncio.timeout(90):
                    response = await client.post(
                        "/api/v1/chat",
                        headers=headers,
                        json={"message": question, "conversation_id": conversation},
                    )
                data = response.json().get("data", {})
                report["cases"].append(
                    {
                        "mode": mode,
                        "question": question,
                        "http_status": response.status_code,
                        "response": data,
                    }
                )
                payload = data["approval"]
                assert payload["actions"][0]["operation"] == "clear_memory"
                result = await client.post(
                    f"/api/v1/chat/conversations/{conversation}/approval?mode={mode}",
                    headers=headers,
                    json={"approval_id": payload["approval_id"], "approved": True},
                )
                assert result.status_code == 200
                async with AsyncSessionLocal() as db:
                    for model in (MemoryCard, MemoryEpisode):
                        assert (
                            await db.scalar(
                                select(func.count())
                                .select_from(model)
                                .where(model.user_id == user.id)
                            )
                            == 0
                        )
                    audit = await db.get(AgentApproval, payload["approval_id"])
                    assert audit.status == "executed"
                assert len(await chat_history_service.get_recent_history(user.id, target)) == 2
                report["checks"].append(
                    {
                        "mode": mode,
                        "check": "clear memory preserves chat and records execution receipt",
                        "passed": True,
                        "final_answer": result.json()["data"]["answer"],
                    }
                )
    except Exception as exc:  # noqa: BLE001 retain an honest failure record and always clean users
        report["error_type"] = type(exc).__name__
    finally:
        settings.AGENT_MODE, settings.CHECKPOINT_ENABLED = original
        async with AsyncSessionLocal() as db:
            await db.execute(delete(User).where(User.id.in_(owners)))
            await db.commit()
            report["cleanup_passed"] = (
                await db.scalar(select(func.count()).select_from(User).where(User.id.in_(owners)))
                == 0
            )
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["passed"] = (
            len(report["cases"]) == 6
            and len(report["checks"]) == 6
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
                    "cases": len(report["cases"]),
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
