r"""短期摘要真实模型/API 验收；使用临时合成账号，结束后删除。

backend 目录：.venv\Scripts\python.exe -m app.scripts.verify_chat_summary
会调用已配置的付费 API，不修改 .env；已有报告拒绝覆盖。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx
from sqlalchemy import delete

from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.chat_summary import ChatSummary
from app.models.user import User
from app.services import chat_history_service, chat_summary_service


async def verify(output: Path) -> None:
    if output.exists():
        raise FileExistsError(f"验收报告已存在，请用 --output 指定新路径：{output}")
    original = (settings.AGENT_MODE, settings.MEMORY_ENABLED, settings.CHAT_SUMMARY_ENABLED)
    users = []
    report = {"at": datetime.now(UTC).isoformat(), "cases": [], "checks": {}, "passed": False}
    try:
        settings.MEMORY_ENABLED = False  # 隔离短期摘要效果，避免长期卡片或 RAG 帮助回忆。
        settings.CHAT_SUMMARY_ENABLED = True
        async with AsyncSessionLocal() as db:
            for label in ("owner", "other"):
                user = User(
                    username=f"summary_live_{label}_{uuid4().hex[:10]}", password_hash="unused"
                )
                db.add(user)
                await db.flush()
                users.append(user.id)
            await db.commit()
        conversation = "summary-live"
        for index in range(1, 10):
            core = (
                "这次活动代号是纸鸢7429，集合点是星河书店，预算原来是500元。"
                if index == 1
                else "更正：这次活动预算改为800元，集合点和代号不变。"
                if index == 3
                else f"第{index}轮补充：这次活动希望少步行，室内优先。"
            )
            _, end = await chat_history_service.add_turn(
                users[0],
                conversation,
                core + "活动细节仍在讨论，需要结合同行者意见再决定具体路线。" * 20,
                "助手建议（用户尚未确认）：可以考虑室内观展，餐厅待定。" * 20,
            )
            await chat_summary_service.check_and_compress(users[0], conversation, end)
        before = await chat_summary_service.get_context(users[0], conversation)
        assert len(before) == 18 and before[0]["role"] == "user"
        report["checks"]["no_summary_before_tenth_turn"] = True
        headers = {"Authorization": f"Bearer {create_access_token(users[0])}"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://live-summary", timeout=180
        ) as client:

            async def ask(mode, path, question, *, auth=headers, conv=conversation):
                settings.AGENT_MODE = mode
                started = perf_counter()
                response = await client.post(
                    path, headers=auth, json={"message": question, "conversation_id": conv}
                )
                response.raise_for_status()
                if path.endswith("stream"):
                    events = [
                        json.loads(line[6:])
                        for line in response.text.splitlines()
                        if line.startswith("data: ")
                    ]
                    assert events[-1]["type"] == "done"
                    answer = "".join(e.get("content", "") for e in events if e["type"] == "token")
                else:
                    data = response.json()
                    assert data["code"] == 0
                    answer = data["data"]["answer"]
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
                print(f"case {len(report['cases'])}: {mode} {path}", flush=True)
                return answer

            # 固定第十轮回复，防止模型复述早期编号后混进最近原文，干扰摘要独立回忆验证。
            _, end = await chat_history_service.add_turn(
                users[0], conversation, "请确认收到。", "收到。"
            )
            assert await chat_summary_service.check_and_compress(users[0], conversation, end)
            print("tenth-turn summary generated", flush=True)
            async with AsyncSessionLocal() as db:
                state = await db.get(ChatSummary, (users[0], conversation))
                assert state and state.version == 1 and state.covered_message_id
                assert all(value in state.summary for value in ("纸鸢7429", "星河书店", "800"))
                report["summary"] = {
                    "content": state.summary,
                    "version": state.version,
                    "characters": len(state.summary),
                }
            compressed = await chat_summary_service.get_context(users[0], conversation)
            assert len(compressed) == 11 and compressed[0]["role"] == "summary"
            assert all("纸鸢7429" not in item["content"] for item in compressed[1:])
            report["checks"]["summary_and_latest_five_turns"] = True
            question = (
                "只根据本会话背景，说出本次活动代号、集合点和更正后的预算；不要调用外部工具。"
            )
            for mode, path in (
                ("single", "/api/v1/chat"),
                ("single", "/api/v1/chat/stream"),
                ("multi", "/api/v1/chat"),
                ("multi", "/api/v1/chat/stream"),
            ):
                answer = await ask(mode, path, question)
                assert all(value in answer for value in ("纸鸢7429", "星河书店", "800"))
            report["checks"]["single_multi_and_sse_recall"] = True
            after = await chat_summary_service.get_context(users[0], conversation)
            assert len(after) == 19  # 到第十四轮，过渡消息仍保留。
            report["checks"]["intermediate_messages_retained"] = True
            other_headers = {"Authorization": f"Bearer {create_access_token(users[1])}"}
            isolated = await ask("multi", "/api/v1/chat", question, auth=other_headers)
            assert "纸鸢7429" not in isolated and "星河书店" not in isolated
            report["checks"]["user_isolation"] = True
            raw = await client.get(f"/api/v1/chat/conversations/{conversation}", headers=headers)
            assert raw.json()["data"]["total"] == 28
            report["checks"]["raw_messages_preserved"] = True
            removed = await client.delete(
                f"/api/v1/chat/conversations/{conversation}", headers=headers
            )
            removed.raise_for_status()
            assert await chat_summary_service.get_context(users[0], conversation) == []
            report["checks"]["conversation_deletion"] = True
            report["passed"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        settings.AGENT_MODE, settings.MEMORY_ENABLED, settings.CHAT_SUMMARY_ENABLED = original
        async with AsyncSessionLocal() as db:
            if users:
                await db.execute(delete(User).where(User.id.in_(users)))
                await db.commit()
        report["temporary_users_cleaned"] = True
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report: {output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[3]
        / "docs/CHAT_SUMMARY_LIVE_RESULTS_2026-10-11.json",
    )
    asyncio.run(verify(parser.parse_args().output))
