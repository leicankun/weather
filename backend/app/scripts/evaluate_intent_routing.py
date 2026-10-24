"""真实 Embedding 路由验收；可选调用匿名聊天 HTTP/SSE，不写开发库或 .env。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.services.intent_examples import INTENT_EXAMPLES
from app.services.intent_router import identify_intent

# These evaluation utterances are kept separate from the router's exemplar corpus.
CALIBRATION_CASES = [
    ("广州明日降雨概率高不高？", ["weather"], []),
    ("周日去深圳会不会很热？", ["weather"], []),
    ("这周末能把被子拿出去晾吗？", ["weather"], []),
    ("明早出门背包要放雨具吗？", ["weather"], []),
    ("早晚温差大，薄衬衫加开衫合适吗？", ["weather", "outfit"], []),
    ("去白云山徒步选什么鞋和衣服比较舒服？", ["weather", "outfit"], []),
    ("南站到沙面坐什么车方便？", ["route"], []),
    ("机场过去乘地铁好还是叫车好？", ["route"], []),
    ("我下周已保存了哪些活动？", ["itinerary"], []),
    ("给我设计一份广州四日游的安排", ["itinerary"], []),
    ("我传过的那份游记提到集合地点了吗？", ["itinerary"], []),
    ("哪里能吃到比较地道的早茶？", ["knowledge"], []),
    ("长隆现在入场要多少钱？", ["knowledge"], []),
    ("沙面有哪些值得看的老建筑？", ["knowledge"], []),
    ("明天广州下不下雨，坐什么车去白云山？", ["weather", "route"], []),
    ("去广州塔的衣服怎么搭配、公共交通如何坐？", ["weather", "outfit", "route"], []),
    ("早上好，很高兴认识你", [], []),
    ("非常感谢，先聊到这儿", [], []),
    ("请介绍一下你能帮我做哪些事情", [], []),
    ("那后天呢？", ["weather"], [{"role": "user", "content": "广州明天天气如何？"}]),
    ("换成深圳呢？", ["weather"], [{"role": "user", "content": "广州明天天气如何？"}]),
    (
        "那后天呢？",
        ["weather", "outfit", "route"],
        [{"role": "user", "content": "明天去广州塔穿什么、怎么走"}],
    ),
    ("广州有什么特色小吃？", ["knowledge"], [{"role": "user", "content": "广州明天天气如何？"}]),
]

ACCEPTANCE_CASES = [
    ("番禺明天雨量大吗？", ["weather"], []),
    ("周末广州最高能到多少摄氏度？", ["weather"], []),
    ("周六室外风速大不大？", ["weather"], []),
    ("明天在广州逛街该穿长袖还是短袖？", ["weather", "outfit"], []),
    ("登山时运动鞋配防风外套合适吗？", ["weather", "outfit"], []),
    ("从体育西路到广州南站乘车需要多少时间？", ["route"], []),
    ("请比较广州站去机场的公交和驾车耗时", ["route"], []),
    ("帮我列一个佛山两日游日程", ["itinerary"], []),
    ("我收藏的旅行笔记里怎么介绍沙面？", ["itinerary"], []),
    ("查询我已经保存的下周日程", ["itinerary"], []),
    ("广州有哪些值得推荐的粤菜馆？", ["knowledge"], []),
    ("广州塔今天的营业时段是什么？", ["knowledge"], []),
    ("最近有没有新的景区活动公告？", ["knowledge"], []),
    ("嗨，能认识你很开心", [], []),
    ("谢谢啦，回头聊", [], []),
    ("讲个轻松的小故事", [], []),
    (
        "后天广州的降水情况如何、爬山穿哪种外套、南站怎么坐车过去？",
        ["weather", "outfit", "route"],
        [],
    ),
    ("这回不用查询天气，只帮我看乘车路线", ["route"], []),
    ("那周日呢？", ["weather"], [{"role": "user", "content": "查广州周六的降雨概率"}]),
    ("改到深圳呢？", ["weather"], [{"role": "user", "content": "查广州周六的降雨概率"}]),
    (
        "那后天呢？",
        ["weather", "outfit", "route"],
        [{"role": "user", "content": "明天去广州塔穿什么、怎么走"}],
    ),
    ("那路线呢？", ["route"], [{"role": "user", "content": "查广州周六的降雨概率"}]),
    (
        "广州最好吃的烧鹅在哪？",
        ["knowledge"],
        [{"role": "user", "content": "查广州周六的降雨概率"}],
    ),
    ("那后天呢？", None, []),
    ("那后天呢？", None, [{"role": "user", "content": "谢谢啦，回头聊"}]),
]


async def run_http():
    import httpx

    from app.main import app

    checks = []
    previous_mode = settings.AGENT_MODE
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://live-test"
        ) as client:
            for mode in ("single", "multi"):
                settings.AGENT_MODE = mode
                for stream in (False, True):
                    started = time.perf_counter()
                    response = await client.post(
                        "/api/v1/chat" + ("/stream" if stream else ""), json={"message": "你是谁？"}
                    )
                    if stream:
                        events = [
                            json.loads(line[6:])
                            for line in response.text.splitlines()
                            if line.startswith("data: ")
                        ]
                        answer = "".join(
                            event.get("content", "") for event in events if event["type"] == "token"
                        )
                        passed = (
                            bool(events)
                            and events[-1]["type"] == "done"
                            and any(event.get("intent") == "other" for event in events)
                        )
                    else:
                        data = response.json().get("data", {})
                        answer = data.get("answer", "")
                        passed = data.get("intent") == "other"
                    checks.append(
                        {
                            "mode": mode,
                            "stream": stream,
                            "status_code": response.status_code,
                            "answer": answer,
                            "passed": response.status_code == 200 and passed and bool(answer),
                            "duration_ms": round((time.perf_counter() - started) * 1000),
                        }
                    )
    finally:
        settings.AGENT_MODE = previous_mode
    return checks


async def main(output: Path, http: bool, suite: str = "acceptance") -> int:
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    rows = []
    # Warm the corpus once before concurrent queries; no query/result is globally cached.
    warm = await identify_intent("你是谁？")
    if warm.status == "unavailable":
        raise RuntimeError("Configured semantic embedding provider is unavailable")
    semaphore = asyncio.Semaphore(3)

    async def evaluate(question, expected, history):
        async with semaphore:
            started = time.perf_counter()
            decision = await identify_intent(question, history)
            return {
                "question": question,
                "history": history,
                "expected": expected,
                **decision.state(),
                "passed": (decision.status == "uncertain" and not decision.domains)
                if expected is None
                else decision.domains == expected and decision.status in {"matched", "general"},
                "duration_ms": round((time.perf_counter() - started) * 1000),
            }

    cases = CALIBRATION_CASES if suite == "calibration" else ACCEPTANCE_CASES
    rows = await asyncio.gather(*(evaluate(*case) for case in cases))
    checks = await run_http() if http else []
    report = {
        "date": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "embedding_model": settings.ZHIPU_EMBED_MODEL,
        "dimension": settings.EMBED_DIM,
        "suite": suite,
        "threshold": settings.INTENT_SIMILARITY_THRESHOLD,
        "multi_margin": settings.INTENT_MULTI_MARGIN,
        "corpus_sha256": hashlib.sha256(
            json.dumps(INTENT_EXAMPLES, ensure_ascii=False).encode()
        ).hexdigest(),
        "passed": sum(row["passed"] for row in rows),
        "total": len(rows),
        "cases": rows,
        "http": checks,
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
                "http_passed": sum(row["passed"] for row in checks),
                "output": str(output),
                "mismatches": [
                    {
                        "question": row["question"],
                        "expected": row["expected"],
                        "actual": row["domains"],
                        "status": row["status"],
                        "scores": row["scores"],
                    }
                    for row in rows
                    if not row["passed"]
                ],
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
        help="Also call real anonymous chat HTTP/SSE (paid LLM calls)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.output, args.http, args.suite)))
