"""真实模型核验验收：合成错误草稿 + 实际 HTTP/SSE 多智能体链路。

backend 中执行 python -m app.scripts.verify_multi_agent。
调用已配置的付费模型及实际工具；匿名请求不写聊天记录，不修改 .env。
输出文件已存在则拒绝覆盖，可用 --output 指定新文件。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.core.config import BACKEND_DIR, settings
from app.services import multi_agent
from app.services.answer_verification import evidence_record


def redact(value: str) -> str:
    for key, secret in settings.model_dump().items():
        if (
            any(word in key for word in ("KEY", "PASSWORD", "SECRET"))
            and isinstance(secret, str)
            and len(secret) >= 6
        ):
            value = value.replace(secret, "[REDACTED]")
    return value


def synthetic_cases() -> list[dict]:
    weather = evidence_record(
        "get_forecast",
        {"city": "广州", "days": 3},
        "合成工具证据：今天阴21.6~31.2℃；明天晴21.6~32.6℃。",
    )
    outfit = evidence_record(
        "recommend_outfit", {"city": "广州"}, "合成工具证据：今天阴，最高31.2℃；建议短袖。"
    )
    route = evidence_record(
        "plan_travel_route",
        {"origin": "广州南站", "destination": "广州塔"},
        "合成工具证据：地铁51分钟票价5元；驾车35分钟估算成本10.3元。",
    )
    return [
        {
            "id": "already_valid",
            "question": "广州明天天气怎么样？",
            "answer": "明天广州晴，21.6~32.6℃。",
            "domain_evidence": {"weather": [weather]},
        },
        {
            "id": "date_conflict",
            "question": "广州明天天气怎么样，怎么穿？",
            "answer": "明天广州阴，21.6~31.2℃，建议短袖。",
            "domain_answers": {"weather": "明天晴21.6~32.6℃", "outfit": "明天阴最高31.2℃"},
            "domain_evidence": {"weather": [weather], "outfit": [outfit]},
        },
        {
            "id": "taxi_cost",
            "question": "广州南站去广州塔，比较地铁和打车的时间、费用。",
            "answer": "地铁51分钟5元；打车35分钟10.3元，包含起步价。请先确认您的出发地是否为广州南站。",
            "domain_evidence": {"route": [route]},
        },
        {
            "id": "unsupported_details",
            "question": "广州塔附近有哪些雨天室内景点？营业时间是什么？",
            "answer": "广东省博物馆离广州塔500米，每天9:00至22:00营业。",
            "domain_evidence": {
                "knowledge": [
                    evidence_record(
                        "search_knowledge",
                        {"query": "广州塔附近室内景点"},
                        "合成工具证据：广东省博物馆是室内场馆。未提供距离或营业时间。",
                    )
                ]
            },
        },
    ]


async def worker(output: Path, http_only: bool = False) -> int:
    from app.main import app

    # HTTP 客户端日志可含鉴权查询参数；父进程仍统一脱敏子进程/MCP 输出。
    logging.disable(logging.CRITICAL)
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "provider": settings.LLM_PROVIDER,
        "model": getattr(settings, f"{settings.LLM_PROVIDER.upper()}_LLM_MODEL", ""),
        "synthetic_cases_note": "人工构造工具证据和错误草稿，核验调用真实模型；不是实际天气或路线。",
        "synthetic_cases": [],
        "http_cases": [],
    }
    failed = False
    for case in [] if http_only else synthetic_cases():
        print(f"review {case['id']}", flush=True)
        result = await multi_agent._verify_node(case)
        report["synthetic_cases"].append({"input": case, "result": result})
        failed |= result["verification"]["status"] not in ("passed", "corrected")

    original_node = multi_agent._verify_node
    observed = []

    async def capture(state):
        result = await original_node(state)
        observed.append(
            {
                "question": state["question"],
                "draft": state.get("answer", ""),
                "domain_answers": state.get("domain_answers", {}),
                "domain_evidence": state.get("domain_evidence", {}),
                "result": result,
            }
        )
        return result

    original_mode = settings.AGENT_MODE
    settings.AGENT_MODE = "multi"
    multi_agent._verify_node = capture
    multi_agent.reset_cache()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for path, question in [
                ("/api/v1/chat", "广州明天天气怎么样？我怕冷，去广州塔怎么穿？"),
                ("/api/v1/chat/stream", "广州南站去广州塔，比较地铁和打车的时间、费用。"),
            ]:
                print(f"http {path}", flush=True)
                before = len(observed)
                started = datetime.now(UTC)
                try:
                    response = await asyncio.wait_for(
                        client.post(path, json={"message": question}), timeout=180
                    )
                    payload = (
                        response.json()
                        if not path.endswith("stream")
                        else [
                            json.loads(line.removeprefix("data: "))
                            for line in response.text.splitlines()
                            if line.startswith("data: ")
                        ]
                    )
                    captured = observed[before:]
                    report["http_cases"].append(
                        {
                            "path": path,
                            "question": question,
                            "status_code": response.status_code,
                            "elapsed_seconds": (datetime.now(UTC) - started).total_seconds(),
                            "response": payload,
                            "verification_trace": captured,
                        }
                    )
                    failed |= response.status_code != 200 or not captured
                    failed |= any(
                        item["result"]["verification"]["status"] not in ("passed", "corrected")
                        for item in captured
                    )
                except Exception as exc:  # noqa: BLE001 保存实际失败，不能生成假结果
                    report["http_cases"].append(
                        {"path": path, "question": question, "error": type(exc).__name__}
                    )
                    failed = True
    finally:
        settings.AGENT_MODE = original_mode
        multi_agent._verify_node = original_node
        multi_agent.reset_cache()
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["execution_ok"] = not failed
    with output.open("x", encoding="utf-8") as target:
        target.write(redact(json.dumps(report, ensure_ascii=False, indent=2)))
    print(f"saved {output.name}; execution_ok={not failed}", flush=True)
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=BACKEND_DIR.parent
        / "docs"
        / f"AGENT_VERIFICATION_LIVE_{datetime.now().date()}.json",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--http-only", action="store_true", help="仅运行实际 HTTP/SSE 链路")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("输出文件已存在，请用 --output 指定新文件，保留原始记录")
    if args.worker:
        return asyncio.run(worker(output, args.http_only))
    # MCP 子进程可能打印含 Key 的 URL；先捕获，再脱敏后输出。
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.scripts.verify_multi_agent",
            "--worker",
            "--output",
            str(output),
            *(["--http-only"] if args.http_only else []),
        ],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(redact(line), end="", flush=True)
    return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
