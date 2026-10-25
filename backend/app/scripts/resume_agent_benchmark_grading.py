"""Resume only missing v1 semantic grades without regenerating any answer.

--plan performs no API calls. --execute appends supplemental grades; original
inference.jsonl and runs.jsonl are immutable. Payment/auth errors stop the run.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import contextvars
import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from app.scripts.agent_benchmark import JUDGE_PROMPT, judge, sanitized
from app.scripts.agent_benchmark_data import ANCHOR
from app.scripts.report_agent_benchmark import merge_grades
from app.services.llm_client import get_llm


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def key(row: dict) -> tuple:
    return row["case_id"], row["repeat"], row["mode"]


def protocol(directory: Path) -> dict:
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    path = directory / "code_snapshot/app/scripts/agent_benchmark.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    saved_prompt = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "JUDGE_PROMPT" for t in node.targets)
    )
    if saved_prompt != JUDGE_PROMPT or metadata["anchor_date"] != ANCHOR.isoformat():
        raise ValueError("Original judge prompt/date does not match; refusing mixed scoring")
    dataset = json.loads((directory / "dataset.json").read_text(encoding="utf-8"))
    dataset_hash = hashlib.sha256(
        json.dumps(dataset, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    if dataset_hash != metadata["dataset_sha256"]:
        raise ValueError("Original dataset changed")
    return {
        "inference_sha256": digest(directory / "inference.jsonl"),
        "original_runs_sha256": digest(directory / "runs.jsonl"),
        "dataset_sha256": dataset_hash,
        "judge_prompt_sha256": hashlib.sha256(saved_prompt.encode()).hexdigest(),
        "provider": metadata["provider"],
        "model": metadata["model"],
        "judge_temperature": 0.0,
        "max_tokens": 1800,
    }


def plan(directory: Path) -> tuple[dict, list[dict]]:
    provenance = protocol(directory)
    plan_path = directory / "grading_recovery_plan.json"
    if plan_path.exists():
        previous = json.loads(plan_path.read_text(encoding="utf-8"))
        if previous["protocol"] != provenance:
            raise ValueError("Original files changed after recovery was planned")
    recovery_meta = directory / "grading_recovery_metadata.json"
    if recovery_meta.exists():
        saved = json.loads(recovery_meta.read_text(encoding="utf-8"))
        if saved["protocol"] != provenance:
            raise ValueError("Recovery protocol or original files changed")
    original = read_lines(directory / "runs.jsonl")
    merged = merge_grades(original, read_lines(directory / "supplemental_grades.jsonl"))
    pending = [row for row in merged if row["quality"]["status"] == "judge_failed"]
    result = {
        "protocol": provenance,
        "original_answers": len(original),
        "original_scored": sum(row["quality"]["status"] == "scored" for row in original),
        "now_scored": sum(row["quality"]["status"] == "scored" for row in merged),
        "pending": len(pending),
        "pending_ids": [list(key(row)) for row in pending],
        "no_answer_regeneration": True,
        "original_successful_grades_reused": True,
    }
    plan_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result, pending


async def execute(directory: Path, workers: int) -> None:
    proposed, pending = plan(directory)
    if not pending:
        print("All original answers already have semantic grades.")
        return
    provenance = proposed["protocol"]
    model = get_llm(provider=provenance["provider"], model=provenance["model"]).model_copy(
        update={"temperature": 0.0, "max_tokens": 1800, "max_retries": 1}
    )
    client_type = type(model.root_async_client._client)
    original_send = client_type.send
    active = contextvars.ContextVar("recovery_network", default=None)

    async def tracked_send(client, request, *args, **kwargs):
        network = active.get()
        started = time.perf_counter()
        record = {
            "kind": "chat",
            "host": request.url.host,
            "path": request.url.path,
            "model": provenance["model"],
        }
        network.append(record)
        try:
            response = await original_send(client, request, *args, **kwargs)
            record["http_status"] = response.status_code
            await response.aread()
            try:
                body = response.json()
                record.update(usage=body.get("usage"), response_model=body.get("model"))
            except ValueError:
                record["usage"] = None
            return response
        except Exception as exc:
            record["error"] = type(exc).__name__
            raise
        finally:
            record["ms"] = round((time.perf_counter() - started) * 1000, 2)

    by_case = {
        c["id"]: c for c in json.loads((directory / "dataset.json").read_text(encoding="utf-8"))
    }
    semaphore = asyncio.Semaphore(workers)
    stopped = asyncio.Event()
    recovered = 0
    meta = {
        "protocol": provenance,
        "started_utc": datetime.now(UTC).isoformat(),
        "status": "running",
    }
    target = directory / "grading_recovery_metadata.json"
    target.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    async def grade_row(row):
        nonlocal recovered
        async with semaphore:
            if stopped.is_set():
                return
            network = []
            token = active.set(network)
            started = time.perf_counter()
            try:
                async with asyncio.timeout(90):
                    quality = await judge(row, by_case[row["case_id"]], model)
            except Exception as exc:  # noqa: BLE001 - retain scoring transport failures
                quality = {
                    "status": "judge_failed",
                    "task_success": False,
                    "covered": 0,
                    "total": len(by_case[row["case_id"]]["criteria"]),
                    "error": type(exc).__name__,
                }
            finally:
                active.reset(token)
            entry = {
                "case_id": row["case_id"],
                "repeat": row["repeat"],
                "mode": row["mode"],
                "quality": quality,
                "judge_network": network,
                "judge_ms": round((time.perf_counter() - started) * 1000, 2),
            }
            with (directory / "supplemental_grades.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(sanitized(json.dumps(entry, ensure_ascii=False)) + "\n")
            if quality.get("terminal_service_error"):
                stopped.set()
                print(
                    f"Stopped on provider HTTP {quality['service_status']}; original files retained.",
                    flush=True,
                )
            elif quality["status"] == "scored":
                recovered += 1
                print(
                    f"Recovered {recovered}/{len(pending)}: {row['case_id']} r{row['repeat']} {row['mode']}",
                    flush=True,
                )

    with patch.object(client_type, "send", tracked_send):
        await asyncio.gather(*(grade_row(row) for row in pending))
    if protocol(directory) != provenance:
        raise ValueError("Original files changed during recovery")
    final_plan, remaining = plan(directory)
    meta.update(
        status="stopped_service_error"
        if stopped.is_set()
        else "complete"
        if not remaining
        else "partial",
        finished_utc=datetime.now(UTC).isoformat(),
        newly_scored=recovered,
        now_scored=final_plan["now_scored"],
        pending=len(remaining),
    )
    target.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        parser.error("workers must be between 1 and 4")
    logging.disable(logging.CRITICAL)
    if args.plan:
        summary, _ = plan(args.directory.resolve())
        print(
            json.dumps(
                {k: v for k, v in summary.items() if k != "pending_ids"},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        asyncio.run(execute(args.directory.resolve(), args.workers))
