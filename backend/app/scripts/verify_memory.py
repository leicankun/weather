r"""双层记忆真实模型验收（会调用付费 API）。

backend 目录：.venv\Scripts\python.exe -m app.scripts.verify_memory
临时账号仅存合成信息，完成后删除；不修改 .env 或已有用户数据。
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx
from sqlalchemy import delete, func, select

from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.memory import MemoryCard, MemoryEpisode
from app.models.user import User
from app.services import chat_history_service


async def main():
    original_mode = settings.AGENT_MODE
    user_ids = []
    report = {"at": datetime.now(UTC).isoformat(), "cases": [], "checks": {}}
    suffix = uuid4().hex[:10]
    try:
        async with AsyncSessionLocal() as db:
            for label in ("owner", "other"):
                user = User(username=f"memory_live_{label}_{suffix}", password_hash="unused")
                db.add(user)
                await db.flush()
                user_ids.append(user.id)
            await db.commit()
        headers = {"Authorization": f"Bearer {create_access_token(user_ids[0])}"}
        other_headers = {"Authorization": f"Bearer {create_access_token(user_ids[1])}"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://live-memory", timeout=180
        ) as client:

            async def request(method, path, *, auth=headers, **kwargs):
                response = await client.request(method, path, headers=auth, **kwargs)
                response.raise_for_status()
                payload = response.json()
                assert payload["code"] == 0, payload
                return payload.get("data")

            async def ask(mode, path, question, conversation, auth=headers):
                settings.AGENT_MODE = mode
                started = perf_counter()
                response = await client.post(
                    path, headers=auth, json={"message": question, "conversation_id": conversation}
                )
                response.raise_for_status()
                if path.endswith("stream"):
                    events = [
                        json.loads(line[6:])
                        for line in response.text.splitlines()
                        if line.startswith("data: ")
                    ]
                    assert events[-1]["type"] == "done"
                    answer = "".join(
                        event.get("content", "") for event in events if event["type"] == "token"
                    )
                else:
                    payload = response.json()
                    assert payload["code"] == 0
                    answer = payload["data"]["answer"]
                assert answer and "服务暂时不可用" not in answer
                report["cases"].append(
                    {
                        "mode": mode,
                        "path": path,
                        "question": question,
                        "answer": answer,
                        "seconds": round(perf_counter() - started, 2),
                    }
                )
                print(
                    f"case {len(report['cases'])}: {mode} {round(perf_counter() - started, 2)}s",
                    flush=True,
                )
                return answer

            await ask(
                "single",
                "/api/v1/chat",
                "我长期住在广州天河区，我平时不吃辣，出门喜欢坐地铁。",
                "profile",
            )
            first_cards = await request("GET", "/api/v1/memory/cards")
            report["first_cards"] = first_cards["items"]
            assert any("天河" in card["data"]["value"] for card in first_cards["items"])
            assert any("不吃辣" in card["data"]["value"] for card in first_cards["items"])
            answer = await ask(
                "single", "/api/v1/chat", "你了解我的居住地和饮食偏好吗？", "profile-recall"
            )
            assert "天河" in answer and ("不吃辣" in answer or "清淡" in answer)
            await ask(
                "single",
                "/api/v1/chat",
                "上次游玩活动的集合点是星河书店，活动暗号是蓝鲸7429，这只是那次活动的信息。",
                "old-event",
            )
            for index in range(12):
                await chat_history_service.add_message(
                    user_ids[0], "old-event", "assistant", f"临时活动的无关占位消息 {index}"
                )
            history = await chat_history_service.get_recent_history(user_ids[0], "old-event")
            assert all("蓝鲸7429" not in item["content"] for item in history)
            answer = await ask(
                "single", "/api/v1/chat", "上次游玩活动的集合点和暗号是什么？", "event-recall"
            )
            assert "星河书店" in answer and "蓝鲸7429" in answer
            hits = await request(
                "POST",
                "/api/v1/memory/search",
                json={"query": "上次游玩活动的集合点和暗号是什么？", "top_k": 3},
            )
            report["retrieval"] = hits["items"]
            assert hits["items"] and all(item["rerank_score"] is not None for item in hits["items"])
            assert any(
                item["dense_rank"] is not None and item["bm25_rank"] is not None
                for item in hits["items"]
            )
            await ask(
                "single",
                "/api/v1/chat",
                "更正一下，我长期住在海珠区，现在喜欢微辣，日常还是坐地铁。",
                "correction",
            )
            updated = await request("GET", "/api/v1/memory/cards")
            report["updated_cards"] = updated["items"]
            assert any("海珠" in card["data"]["value"] for card in updated["items"])
            assert not any("天河" in card["data"]["value"] for card in updated["items"])
            assert any("微辣" in card["data"]["value"] for card in updated["items"])
            answer = await ask(
                "single", "/api/v1/chat", "我现在住哪，喜欢吃什么口味？", "after-correction"
            )
            assert "海珠" in answer and "微辣" in answer
            answer = await ask(
                "multi", "/api/v1/chat/stream", "记得我现在住哪、喜欢什么口味吗？", "multi-profile"
            )
            assert "海珠" in answer and "微辣" in answer
            answer = await ask(
                "multi", "/api/v1/chat/stream", "之前那次活动的集合点和暗号是什么？", "multi-event"
            )
            assert "星河书店" in answer and "蓝鲸7429" in answer
            await ask(
                "multi",
                "/api/v1/chat/stream",
                "根据我的常住地和口味，给两条通用饮食建议，不需要查询餐厅。",
                "multi-advice",
            )
            await ask(
                "multi",
                "/api/v1/chat/stream",
                "假设我住在白云区而且对花生过敏，你先不用给建议，这只是举例。",
                "hypothetical",
            )
            after_hypothesis = await request("GET", "/api/v1/memory/cards")
            assert after_hypothesis["items"] == updated["items"], "假设不应改写卡片"
            answer = await ask(
                "multi",
                "/api/v1/chat/stream",
                "我之前活动的暗号是什么？",
                "other-user",
                other_headers,
            )
            assert "蓝鲸7429" not in answer
            assert "无法查看历史记录" not in answer
            await request("DELETE", "/api/v1/memory")
            await request("POST", "/api/v1/memory/backfill")
            assert (await request("GET", "/api/v1/memory/cards"))["items"] == []
            assert (await request("POST", "/api/v1/memory/search", json={"query": "暗号蓝鲸"}))[
                "items"
            ] == []
            answer = await ask(
                "multi", "/api/v1/chat/stream", "你记得我的常住地和饮食偏好吗？", "after-clear"
            )
            assert "海珠" not in answer and "天河" not in answer
            assert "没有记忆功能" not in answer
            report["checks"] = {
                "cross_conversation_cards": True,
                "beyond_ten_messages_rag": True,
                "dense_bm25_rrf_rerank": True,
                "correction": True,
                "multi_sse": True,
                "hypothesis_ignored": True,
                "user_isolation": True,
                "clear_prevents_backfill": True,
            }
    finally:
        settings.AGENT_MODE = original_mode
        if user_ids:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(User).where(User.id.in_(user_ids)))
                await db.commit()
                remaining_users = await db.scalar(
                    select(func.count()).select_from(User).where(User.id.in_(user_ids))
                )
                remaining_cards = await db.scalar(
                    select(func.count())
                    .select_from(MemoryCard)
                    .where(MemoryCard.user_id.in_(user_ids))
                )
                remaining_episodes = await db.scalar(
                    select(func.count())
                    .select_from(MemoryEpisode)
                    .where(MemoryEpisode.user_id.in_(user_ids))
                )
                report["temporary_data_removed"] = (
                    remaining_users == remaining_cards == remaining_episodes == 0
                )
        output = (
            Path(__file__).resolve().parents[3] / "docs" / "MEMORY_LIVE_RESULTS_2026-10-10.json"
        )
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report: {output}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
