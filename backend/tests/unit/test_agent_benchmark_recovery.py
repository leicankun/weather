"""Recover grades only; never overwrite already graded or regenerate answers."""

import copy
import hashlib
import json

import pytest

from app.scripts.agent_benchmark import JUDGE_PROMPT
from app.scripts.agent_benchmark_data import ANCHOR
from app.scripts.report_agent_benchmark import merge_grades
from app.scripts.resume_agent_benchmark_grading import plan


def samples():
    original = [
        {
            "case_id": "w01",
            "repeat": 1,
            "mode": "single",
            "answer": "original A",
            "quality": {"status": "scored", "task_success": True},
        },
        {
            "case_id": "w01",
            "repeat": 1,
            "mode": "multi",
            "answer": "original B",
            "quality": {"status": "judge_failed", "task_success": False},
            "judge_network": [{"http_status": 402}],
        },
    ]
    extra = {
        "case_id": "w01",
        "repeat": 1,
        "mode": "multi",
        "quality": {"status": "scored", "task_success": True},
        "judge_network": [{"http_status": 200}],
        "judge_ms": 200,
    }
    return original, extra


def test_recovery_preserves_original_success_and_raw_answers_without_mutation():
    original, extra = samples()
    before = copy.deepcopy(original)
    merged = merge_grades(original, [extra])
    assert original == before
    assert merged[0] is original[0]
    assert merged[1]["answer"] == "original B" and merged[1]["quality"]["task_success"]
    assert [n["http_status"] for n in merged[1]["judge_network"]] == [402, 200]


@pytest.mark.parametrize("kind", ["already_scored", "unknown_id", "duplicate"])
def test_recovery_rejects_overwrites_unknown_rows_and_duplicate_successes(kind):
    original, extra = samples()
    if kind == "already_scored":
        extra["mode"] = "single"
    elif kind == "unknown_id":
        extra["case_id"] = "other"
    with pytest.raises(ValueError):
        merge_grades(original, [extra, extra] if kind == "duplicate" else [extra])


def test_failed_supplement_remains_unknown_and_does_not_replace_original():
    original, extra = samples()
    extra["quality"] = {"status": "judge_failed", "task_success": False}
    merged = merge_grades(original, [extra])
    assert merged == original


def test_recovery_plan_rejects_later_edits_to_original_answers(tmp_path):
    dataset = [{"id": "w01"}]
    encoded = json.dumps(dataset, ensure_ascii=False, sort_keys=True).encode()
    metadata = {
        "anchor_date": ANCHOR.isoformat(),
        "dataset_sha256": hashlib.sha256(encoded).hexdigest(),
        "provider": "deepseek",
        "model": "deepseek-chat",
    }
    (tmp_path / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (tmp_path / "dataset.json").write_text(json.dumps(dataset), encoding="utf-8")
    (tmp_path / "inference.jsonl").write_text("original immutable answers", encoding="utf-8")
    source = tmp_path / "code_snapshot/app/scripts/agent_benchmark.py"
    source.parent.mkdir(parents=True)
    source.write_text("JUDGE_PROMPT = " + repr(JUDGE_PROMPT), encoding="utf-8")
    original, _ = samples()
    raw = tmp_path / "runs.jsonl"
    raw.write_text("".join(json.dumps(r) + "\n" for r in original), encoding="utf-8")
    proposed, pending = plan(tmp_path)
    assert proposed["original_scored"] == 1 and len(pending) == 1
    original[0]["answer"] = "edited after planning"
    raw.write_text("".join(json.dumps(r) + "\n" for r in original), encoding="utf-8")
    with pytest.raises(ValueError, match="changed after recovery"):
        plan(tmp_path)
