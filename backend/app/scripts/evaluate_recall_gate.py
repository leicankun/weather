"""真实 Embedding 回忆触发评测；不读写数据库，不覆盖旧报告。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.services.recall_gate import RECALL_EXAMPLES, recall_gate

CALIBRATION = [
    ("我原来提过的那间面馆叫什么名字？", True, True, []),
    ("那家适合我口味的店叫什么？", True, True, []),
    ("帮我想想我当时选定的集合地点", True, True, []),
    ("我有没有告诉过你我住哪个区？", True, True, []),
    ("我以前不能吃的东西有哪些？", True, True, []),
    ("我的旅行预算定的是多少来着？", True, True, []),
    ("用我原先说的酒店作为起点，查去白云山的路线", True, False, []),
    ("按我平时喜欢的菜系推荐几家新餐馆", True, False, []),
    ("延续我们讨论过的安排，看看下周天气适不适合", True, False, []),
    ("广州历史上有什么著名建筑？", False, False, []),
    ("以前的人是怎么预测天气的？", False, False, []),
    ("请记得在路线建议里写上耗时", False, False, []),
    ("广州周日会不会有阵雨？", False, False, []),
    ("给我推荐适合带孩子玩的地方", False, False, []),
    ("再见，非常感谢", False, False, []),
    ("那后来呢？", True, True, [{"role": "user", "content": "我上次说过的餐厅叫什么？"}]),
    ("那后天呢？", False, False, [{"role": "user", "content": "广州明天天气怎么样？"}]),
    ("那后来呢？", False, False, []),
]

ACCEPTANCE = [
    ("咱们当时商量去吃饭的地方叫什么来着？", True, True, []),
    ("我常去的那间早茶店是哪家？", True, True, []),
    ("你知道我的忌口有哪些吗？", True, True, []),
    ("回顾一下我曾经给你的出游要求", True, True, []),
    ("我告诉你的出发时间还找得到吗？", True, True, []),
    ("我订的集合点名字忘了，帮忙找找", True, True, []),
    ("参考我的饮食偏好，推荐一家粤菜馆", True, False, []),
    ("沿用我们聊过的起点，规划去机场的交通", True, False, []),
    ("按我原定的出游日期查询当地天气", True, False, []),
    ("沙面以前有哪些历史故事？", False, False, []),
    ("历史上最早的天气观测是什么时候？", False, False, []),
    ("记得把今天广州的气温写清楚", False, False, []),
    ("番禺明天有雨吗？", False, False, []),
    ("广州南到机场要多久？", False, False, []),
    ("请介绍一下你可以做什么", False, False, []),
    ("再详细说一下", True, True, [{"role": "user", "content": "我给你说的集合时间是几点？"}]),
    ("那明天呢？", False, False, [{"role": "user", "content": "广州明天天气怎么样？"}]),
    ("还有细节吗？", False, False, [{"role": "user", "content": "谢谢你"}]),
    ("那后来呢？", False, False, [{"role": "assistant", "content": "我上次说过的餐厅叫什么？"}]),
    ("那家餐厅叫什么？", True, True, []),
]


async def run_http():
    """Real models/graphs/HTTP; auth and memory persistence use synthetic fixtures."""
    import httpx

    from app.core.deps import get_optional_user
    from app.main import app
    from app.routers import chat
    from app.services import memory_service

    class DB:
        async def scalars(self, statement):
            assert 17 in statement.compile().params.values()
            return SimpleNamespace(all=lambda: [])

    @asynccontextmanager
    async def session():
        yield DB()

    async def search(user_id, query, top_k, excluded):
        assert user_id == 17
        return [
            {
                "source": "chat:synthetic:7",
                "content": "2026-10-20 | user | 合成消息7：我说的餐厅名字是云舟茶馆，约在下午三点见。",
            }
        ]

    checks = []
    overrides = dict(app.dependency_overrides)
    try:
        app.dependency_overrides[get_optional_user] = lambda: SimpleNamespace(id=17)
        with ExitStack() as stack:
            for key, value in {
                "MEMORY_ENABLED": True,
                "CHECKPOINT_ENABLED": False,
                "CHAT_SUMMARY_ENABLED": False,
            }.items():
                stack.enter_context(patch.object(settings, key, value))
            for target, key, value in [
                (memory_service, "list_cards", AsyncMock(return_value=[])),
                (memory_service, "index_history", AsyncMock(return_value={})),
                (memory_service, "AsyncSessionLocal", session),
                (memory_service, "search_history", AsyncMock(side_effect=search)),
                (chat, "_save_turn", AsyncMock()),
                (chat, "_with_preferences", AsyncMock(side_effect=lambda prompt, uid: prompt)),
                (chat.chat_summary_service, "get_context", AsyncMock(return_value=[])),
            ]:
                stack.enter_context(patch.object(target, key, value))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://live-test"
            ) as client:
                for mode in ("single", "multi"):
                    with patch.object(settings, "AGENT_MODE", mode):
                        for stream in (False, True):
                            before = memory_service.search_history.await_count
                            response = await client.post(
                                "/api/v1/chat" + ("/stream" if stream else ""),
                                json={"message": "那家餐厅叫什么？"},
                            )
                            if stream:
                                events = [
                                    json.loads(line[6:])
                                    for line in response.text.splitlines()
                                    if line.startswith("data: ")
                                ]
                                answer = "".join(
                                    event.get("content", "")
                                    for event in events
                                    if event["type"] == "token"
                                )
                                completed = bool(events) and events[-1]["type"] == "done"
                            else:
                                answer = response.json().get("data", {}).get("answer", "")
                                completed = response.json().get("code") == 0
                            searches = memory_service.search_history.await_count - before
                            checks.append(
                                {
                                    "mode": mode,
                                    "stream": stream,
                                    "status": response.status_code,
                                    "answer": answer,
                                    "searches": searches,
                                    "passed": response.status_code == 200
                                    and completed
                                    and "云舟茶馆" in answer
                                    and searches == 1,
                                }
                            )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)
    return checks


async def main(output: Path, suite: str, http: bool = False):
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    warm = await recall_gate.decide("你好呀")
    if warm.status == "unavailable":
        raise RuntimeError("Real embedding provider unavailable")
    semaphore = asyncio.Semaphore(3)

    async def evaluate(question, expected, history_only, history):
        async with semaphore:
            decision = await recall_gate.decide(question, history)
            return {
                "question": question,
                "history": history,
                "expected_recall": expected,
                "expected_history_only": history_only,
                **decision.state(),
                "passed": decision.status != "unavailable"
                and decision.need_recall == expected
                and decision.history_only == history_only,
            }

    cases = CALIBRATION if suite == "calibration" else ACCEPTANCE
    rows = await asyncio.gather(*(evaluate(*case) for case in cases))
    checks = await run_http() if http else []
    report = {
        "date": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "suite": suite,
        "model": settings.ZHIPU_EMBED_MODEL,
        "dimension": settings.EMBED_DIM,
        "threshold": settings.MEMORY_RECALL_THRESHOLD,
        "corpus_sha256": hashlib.sha256(
            json.dumps(RECALL_EXAMPLES, ensure_ascii=False).encode()
        ).hexdigest(),
        "passed": sum(row["passed"] for row in rows),
        "total": len(rows),
        "cases": rows,
        "http": checks,
        "http_scope": "Real Embedding/LLM/graphs; synthetic authentication and memory repository; no PostgreSQL writes",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "total": len(rows),
                "output": str(output),
                "http_passed": sum(row["passed"] for row in checks),
                "mismatches": [row for row in rows if not row["passed"]],
            },
            ensure_ascii=False,
        )
    )
    return int(report["passed"] != len(rows) or any(not row["passed"] for row in checks))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--suite", choices=["calibration", "acceptance"], default="acceptance")
    parser.add_argument(
        "--http",
        action="store_true",
        help="Real chat models/HTTP with synthetic memory repository; paid calls",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.output, args.suite, args.http)))
