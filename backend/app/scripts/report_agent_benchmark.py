"""Recompute aggregate metrics and readable reports from immutable raw trials."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    location = (len(ordered) - 1) * q
    low = int(location)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (location - low)


def aggregate(rows: list[dict]) -> dict:
    n = len(rows)
    total_criteria = sum(r["quality"]["total"] for r in rows)
    requirements = sum(r["tools"]["required_groups"] for r in rows)
    calls = sum(r["tools"]["executed_calls"] for r in rows)
    missing_grades = sum(r["quality"]["status"] == "judge_failed" for r in rows)
    by_case = {}
    for row in rows:
        by_case.setdefault(row["case_id"], []).append(row)
    return {
        "runs": n,
        "answer_returned": sum(
            bool(r.get("answer", "").strip()) and not r.get("error") for r in rows
        ),
        "execution_errors": sum(bool(r.get("error")) for r in rows),
        "grading_failures": sum(r["quality"]["status"] == "judge_failed" for r in rows),
        "task_passed": sum(r["quality"]["task_success"] for r in rows),
        "quality_evaluated_runs": n - missing_grades,
        "quality_complete": missing_grades == 0,
        "task_pass_rate": sum(r["quality"]["task_success"] for r in rows) / n
        if n and not missing_grades
        else None,
        "criteria_covered": sum(r["quality"]["covered"] for r in rows),
        "criteria_total": total_criteria,
        "criteria_coverage": sum(r["quality"]["covered"] for r in rows) / total_criteria
        if total_criteria and not missing_grades
        else None,
        "unsupported_answers": sum(bool(r["quality"].get("unsupported_claims")) for r in rows),
        "contradictory_answers": sum(bool(r["quality"].get("contradictions")) for r in rows),
        "required_groups_covered": sum(r["tools"]["covered_groups"] for r in rows),
        "required_groups_total": requirements,
        "required_tool_coverage": sum(r["tools"]["covered_groups"] for r in rows) / requirements
        if requirements
        else None,
        "tool_parameter_correct": sum(r["tools"]["parameter_correct_calls"] for r in rows),
        "executed_tool_calls": calls,
        "tool_parameter_correct_rate": sum(r["tools"]["parameter_correct_calls"] for r in rows)
        / calls
        if calls
        else None,
        "unnecessary_calls": sum(r["tools"]["unnecessary_calls"] for r in rows),
        "attempted_calls": sum(r["tools"]["attempted_calls"] for r in rows),
        "attempted_not_executed": sum(r["tools"]["attempted_not_executed"] for r in rows),
        "repeated_same_arguments": sum(r["tools"]["repeated_same_arguments"] for r in rows),
        "graph_ms_mean": statistics.mean(r["graph_ms"] for r in rows) if n else 0,
        "graph_ms_p50": percentile([r["graph_ms"] for r in rows], 0.5),
        "graph_ms_p95": percentile([r["graph_ms"] for r in rows], 0.95),
        "chat_tokens_total": sum(r["chat_usage"]["total_tokens"] for r in rows),
        "chat_input_tokens": sum(r["chat_usage"]["input_tokens"] for r in rows),
        "chat_output_tokens": sum(r["chat_usage"]["output_tokens"] for r in rows),
        "chat_tokens_mean": statistics.mean(r["chat_usage"]["total_tokens"] for r in rows)
        if n
        else 0,
        "model_calls": sum(len(r["models"]) for r in rows),
        "chat_http_attempts": sum(r["chat_usage"]["attempts"] for r in rows),
        "chat_calls_missing_usage": sum(r["chat_usage"]["missing_usage_calls"] for r in rows),
        "embedding_tokens_total": sum(r["embedding_usage"]["total_tokens"] for r in rows),
        "embedding_http_attempts": sum(r["embedding_usage"]["attempts"] for r in rows),
        "embedding_calls_missing_usage": sum(
            r["embedding_usage"]["missing_usage_calls"] for r in rows
        ),
        "cases_all_repeats_pass": sum(
            all(r["quality"]["task_success"] for r in group) for group in by_case.values()
        ),
        "unique_cases": len(by_case),
        "observed_response_models": dict(
            Counter(m.get("model") or "unknown" for r in rows for m in r["models"])
        ),
    }


def audit(directory: Path, rows: list[dict], metadata: dict, dataset: list[dict]) -> dict:
    keys = [(r["case_id"], r["repeat"], r["mode"]) for r in rows]
    assert len(keys) == len(set(keys)), "Duplicate trial ids"
    assert len(rows) == metadata["planned_runs"], "Incomplete trial count"
    assert {r["case_id"] for r in rows} == {c["id"] for c in dataset}
    assert all(
        sum(k[0] == c["id"] and k[2] == mode for k in keys) == metadata["repeats"]
        for c in dataset
        for mode in ["single", "multi"]
    )
    for row in rows:
        for kind in ["chat", "embedding"]:
            network = [c for c in row["network"] if c["kind"] == kind]
            total = sum(
                int(c.get("usage", {}).get("total_tokens", 0) or 0)
                for c in network
                if isinstance(c.get("usage"), dict)
            )
            assert row[f"{kind}_usage"]["total_tokens"] == total
        if row["quality"]["status"] == "scored":
            expected = next(c for c in dataset if c["id"] == row["case_id"])
            assert sorted(c["id"] for c in row["quality"]["criteria"]) == sorted(
                c["id"] for c in expected["criteria"]
            )
    snapshot_ok = all(
        hashlib.sha256((directory / "code_snapshot" / name).read_bytes()).hexdigest() == digest
        for name, digest in metadata["source_sha256"].items()
    )
    assert snapshot_ok, "Source snapshot hash mismatch"
    dataset_hash = hashlib.sha256(
        json.dumps(dataset, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    assert dataset_hash == metadata["dataset_sha256"]
    return {
        "unique_complete_trial_grid": True,
        "raw_usage_recomputed": True,
        "rubric_ids_complete": True,
        "source_snapshot_sha256_valid": snapshot_ok,
        "dataset_sha256_valid": True,
        "runs": len(rows),
    }


def report(directory: Path) -> dict:
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    dataset = json.loads((directory / "dataset.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (directory / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    checks = audit(directory, rows, metadata, dataset)
    modes = {
        mode: aggregate([r for r in rows if r["mode"] == mode]) for mode in ["single", "multi"]
    }
    categories = {
        category: {
            mode: aggregate([r for r in rows if r["mode"] == mode and r["category"] == category])
            for mode in modes
        }
        for category in sorted({r["category"] for r in rows})
    }
    pairs = {}
    for row in rows:
        pairs.setdefault((row["case_id"], row["repeat"]), {})[row["mode"]] = row
    pairing = Counter()
    for pair in pairs.values():
        if any(r["quality"]["status"] == "judge_failed" for r in pair.values()):
            pairing["not_fully_graded"] += 1
            continue
        s, m = pair["single"]["quality"]["task_success"], pair["multi"]["quality"]["task_success"]
        pairing[
            "both_pass" if s and m else "single_only" if s else "multi_only" if m else "both_fail"
        ] += 1
    judgment = [c for r in rows for c in r["judge_network"]]
    warmup = json.loads((directory / "warmup.json").read_text(encoding="utf-8"))
    result = {
        "modes": modes,
        "categories": categories,
        "paired_outcomes": dict(pairing),
        "audit": checks,
        "judge_calls": len(judgment),
        "judge_reported_tokens": sum(
            int(c.get("usage", {}).get("total_tokens", 0) or 0)
            for c in judgment
            if isinstance(c.get("usage"), dict)
        ),
        "warmup_calls": len(warmup["network"]),
        "warmup_reported_tokens": sum(
            int(c.get("usage", {}).get("total_tokens", 0) or 0)
            for c in warmup["network"]
            if isinstance(c.get("usage"), dict)
        ),
    }
    offline_path = directory / "offline_summary.json"
    offline = (
        json.loads(offline_path.read_text(encoding="utf-8")) if offline_path.exists() else None
    )
    if offline:
        result["offline_keypoints"] = offline
        offline_rows = [
            json.loads(line)
            for line in (directory / "offline_scores.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        result["offline_categories"] = {
            category: {
                mode: {
                    "runs": sum(
                        r["category"] == category and r["mode"] == mode for r in offline_rows
                    ),
                    "passed": sum(
                        r["category"] == category and r["mode"] == mode and r["all_keypoints_pass"]
                        for r in offline_rows
                    ),
                }
                for mode in modes
            }
            for category in categories
        }
    (directory / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (directory / "audit.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    def pct(value):
        return f"{value:.1%}" if value is not None else "—"

    lines = [
        "# 天气出行 Agent 单、多智能体联合评测",
        "",
        f"数据版本：{metadata['version']}；固定日期：{metadata['anchor_date']}；模型配置：{metadata['provider']} / {metadata['model']}。",
        f"{metadata['cases']} 个固定合成场景，每种模式每题 {metadata['repeats']} 次，共 {len(rows)} 次执行。按固定随机种子交错任务，最多 {metadata['workers']} 个图请求并发；全部生成结束后再自动评分。",
        "",
        "本报告比较当前完整单/多智能体系统：使用真实模型、真实语义路由 Embedding 和现有 LangGraph；业务工具保持原名称、描述及参数结构，执行替换为同一份冻结合成返回值，每次模拟等待20毫秒。不调用真实天气、地图、搜索或数据库。质量由同一个配置模型在不知道单/多模式的情况下按预设规则自动评审，不是独立人工准确率。",
        "",
        "## 三项结果",
        "",
        "| 指标 | 单智能体 | 多智能体 |",
        "|---|---:|---:|",
    ]
    s, m = modes["single"], modes["multi"]

    def quality_cell(bucket):
        if not bucket["quality_complete"]:
            return f"未完成（已评分{bucket['quality_evaluated_runs']}/{bucket['runs']}，不计算总体通过率）"
        return f"{bucket['task_passed']}/{bucket['runs']}（{pct(bucket['task_pass_rate'])}）"

    if not s["quality_complete"] or not m["quality_complete"]:
        lines[2:2] = [
            "**完整模型语义评分未完成：评分接口余额不足（HTTP 402），只完成33/240。未评分不能记作Agent答错。另已对全部240份回答完成可复算的离线关键要点检查；它不等于全面事实正确率或无幻觉率。**",
            "",
        ]
        metadata.update(
            status="quality_incomplete",
            inference_status="complete",
            quality_status="partial_insufficient_balance",
        )
        (directory / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    items = [
        (
            "有回答且无图异常",
            f"{s['answer_returned']}/{s['runs']}",
            f"{m['answer_returned']}/{m['runs']}",
        ),
        (
            "完整任务通过（模型语义评分，未完成）",
            quality_cell(s),
            quality_cell(m),
        ),
        ("模型语义要点覆盖（未完成）", pct(s["criteria_coverage"]), pct(m["criteria_coverage"])),
        (
            "已完成模型评分中检出的无依据事实回答数",
            str(s["unsupported_answers"]),
            str(m["unsupported_answers"]),
        ),
        (
            "已完成模型评分中检出的事实冲突回答数",
            str(s["contradictory_answers"]),
            str(m["contradictory_answers"]),
        ),
        (
            "必要工具能力覆盖",
            f"{s['required_groups_covered']}/{s['required_groups_total']}（{pct(s['required_tool_coverage'])}）",
            f"{m['required_groups_covered']}/{m['required_groups_total']}（{pct(m['required_tool_coverage'])}）",
        ),
        (
            "已执行调用的检查项参数正确率",
            pct(s["tool_parameter_correct_rate"]),
            pct(m["tool_parameter_correct_rate"]),
        ),
        (
            "已执行 / 模型提出的工具调用",
            f"{s['executed_tool_calls']} / {s['attempted_calls']}",
            f"{m['executed_tool_calls']} / {m['attempted_calls']}",
        ),
        ("任务范围外的已执行调用", str(s["unnecessary_calls"]), str(m["unnecessary_calls"])),
        (
            "同一题内相同参数重复调用",
            str(s["repeated_same_arguments"]),
            str(m["repeated_same_arguments"]),
        ),
        (
            "图执行平均耗时 / 秒",
            f"{s['graph_ms_mean'] / 1000:.2f}",
            f"{m['graph_ms_mean'] / 1000:.2f}",
        ),
        (
            "图执行 P50 / P95 秒（全体尝试）",
            f"{s['graph_ms_p50'] / 1000:.2f} / {s['graph_ms_p95'] / 1000:.2f}",
            f"{m['graph_ms_p50'] / 1000:.2f} / {m['graph_ms_p95'] / 1000:.2f}",
        ),
        (
            "每次平均聊天 Token（不含评分和Embedding）",
            f"{s['chat_tokens_mean']:.1f}",
            f"{m['chat_tokens_mean']:.1f}",
        ),
        (
            "模型调用 / 聊天HTTP尝试",
            f"{s['model_calls']} / {s['chat_http_attempts']}",
            f"{m['model_calls']} / {m['chat_http_attempts']}",
        ),
        (
            "路由 Embedding Token / HTTP尝试",
            f"{s['embedding_tokens_total']} / {s['embedding_http_attempts']}",
            f"{m['embedding_tokens_total']} / {m['embedding_http_attempts']}",
        ),
        (
            "聊天 / Embedding 缺失usage的尝试数",
            f"{s['chat_calls_missing_usage']} / {s['embedding_calls_missing_usage']}",
            f"{m['chat_calls_missing_usage']} / {m['embedding_calls_missing_usage']}",
        ),
        (
            "模型语义评分：全部重复均通过的案例",
            f"{s['cases_all_repeats_pass']}/{s['unique_cases']}"
            if s["quality_complete"]
            else "未完成",
            f"{m['cases_all_repeats_pass']}/{m['unique_cases']}"
            if m["quality_complete"]
            else "未完成",
        ),
        ("因评分服务失败而未评分的回答数", str(s["grading_failures"]), str(m["grading_failures"])),
    ]
    if offline:
        os, om = offline["modes"]["single"], offline["modes"]["multi"]
        items[1:1] = [
            (
                "全部关键要点满足（离线规则v2，不含全面语义判断）",
                f"{os['keypoint_passed']}/{os['runs']}（{pct(os['keypoint_pass_rate'])}）",
                f"{om['keypoint_passed']}/{om['runs']}（{pct(om['keypoint_pass_rate'])}）",
            ),
            (
                "关键要点覆盖（离线规则v2）",
                f"{os['covered']}/{os['total']}（{pct(os['coverage'])}）",
                f"{om['covered']}/{om['total']}（{pct(om['coverage'])}）",
            ),
        ]
    lines.extend(f"| {name} | {one} | {many} |" for name, one, many in items)
    if offline:
        lines += [
            "",
            "离线方法在余额不足后补充，版本为offline-task-checklist-v2，规则和逐项结果分别在code_snapshot/offline_checker_v2.py、offline_scores.jsonl。按用户实际要求修正了三处原rubric过严边界：f04未要求雨天理由；f02只问地铁不要求驾车；r05允许明示参考起点并询问真实出发地，不强制广州中心城区。保持所有原始推理和33份模型评分不变，不混用两种分数。离线检查涵盖预设数值、数据缺失措辞、已声明的约束和特定失败场景，不能证明回答中的每个事实都有依据。",
            "",
            "离线未通过：单智能体m06的三次四领域复合任务（漏查场馆或核验后未发布）及r06的三次路线服务失败场景（先称无法查询，随后仍补充具体路线/时长/票价）；多智能体f06的三次无上下文追问（擅自将话题限定为路线）。更多语义问题和抽查边界见REVIEW_NOTES.md。",
        ]
    lines += [
        "",
        "参数正确率只检查数据集中明确列出的城市、日期、路线起终点及怕冷偏好等约束，不是全参数语义准确率。工具覆盖允许等价路径：穿搭/路线工具已提供完整天气时无需再单独调用天气；工具返回业务错误仍可算已尝试正确能力，最终是否妥善回答由任务质量判定。模型请求在日期校验或预算处被拒绝的次数另存，不能与实际业务执行混算。",
        "",
        "## 分场景离线关键要点满足" if offline else "## 分场景任务通过",
        "",
        "| 场景 | 单智能体 | 多智能体 |",
        "|---|---:|---:|",
    ]
    names = {
        "weather": "天气",
        "outfit": "穿搭",
        "route": "路线",
        "knowledge": "资料/时效信息",
        "composite": "复合任务",
        "followup": "追问/更正/话题切换",
    }
    for category, bucket in categories.items():
        one, many = bucket["single"], bucket["multi"]
        if offline:
            item = result["offline_categories"][category]
            lines.append(
                f"| {names[category]} | {item['single']['passed']}/{item['single']['runs']} | {item['multi']['passed']}/{item['multi']['runs']} |"
            )
        else:
            lines.append(f"| {names[category]} | {quality_cell(one)} | {quality_cell(many)} |")
    lines += [
        "",
        f"同题同重复配对（仅完整评分的配对）：均通过 {pairing['both_pass']}；仅单通过 {pairing['single_only']}；仅多通过 {pairing['multi_only']}；均未通过 {pairing['both_fail']}；另有 {pairing['not_fully_graded']} 组未完整评分。",
        "",
        "## 评分与统计口径",
        "",
        "- 完整任务通过：全部预设要点满足、回答行为正确、未检出无依据事实或事实冲突；图失败与空回答保留为任务失败。评分基础设施失败视为未知，原始记录中的占位task_success=false不能解释为Agent错误；评分不全时不计算总体质量通过率。",
        "- 自动评审逐项理由保存在 runs.jsonl。温度0仍不保证绝对可重复，同模型评审也可能偏袒或误判；不得直接称生产准确率。",
        "- 耗时包含初始状态构造、真实向量路由、模型工具循环、20ms模拟工具等待及系统原有核验；不含一次性工具清单加载、图构建、示例向量预热和后续评分。未测浏览器延迟、SSE首字、记忆准备/落库或真实外部服务延迟。",
        "- 聊天usage直接取供应商HTTP响应，并与回调记录分别保存；重试逐次保留。Embedding与聊天Token分开，未按未知单价估算人民币。缺失usage不等于零费用。",
        "- 每题重复运行用于观察波动，重复结果不是新增独立题；小题集不计算夸大的显著性，也不等同大规模生产压测。P95用线性插值。",
        "- 当前多智能体专业回答始终核验，单智能体按现有逻辑仅在日期相关工具场景核验；提示词、工具分组和预算也不同。本次是完整系统对照，不能将差异单独归因于并行或某一个组件。",
        "- 未改生产代码或.env；模型工具仅在独立评测进程替换；所有场景为合成，未读写数据库。六个追问场景使用预设历史，未测自动摘要和长期记忆写入。",
        "",
        f"评分独立开销：{result['judge_calls']}次HTTP尝试，供应商报告聊天Token {result['judge_reported_tokens']}。示例预热：{result['warmup_calls']}次请求，报告Token {result['warmup_reported_tokens']}（详见warmup.json）。",
        f"供应商响应实际模型名称：单 {s['observed_response_models']}；多 {m['observed_response_models']}。",
        "",
        "## 原始材料与复算",
        "",
        "dataset.json：固定问题、评分要点、允许工具和参数约束。tool_schemas.json：本次真实MCP/本地工具名称与参数结构。inference.jsonl：评分前冻结的回答、工具执行、模型调用及供应商usage。runs.jsonl：附加自动评分和原文。summary.json：统计。code_snapshot/：此次代码与SHA256。audit.json：完整运行网格、用量、题集与代码哈希复核。failures.md：失败记录。",
        "",
        "复算命令：`python -m app.scripts.report_agent_benchmark <结果目录>`。重跑应使用新目录；执行脚本 `python -m app.scripts.agent_benchmark --output <新目录> --repeats 3 --workers 4` 会产生真实模型/Embedding调用。已有目录默认拒绝覆盖，--resume仅允许相同题集与代码续跑。",
        "",
    ]
    (directory / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    failures = [
        "# 未通过记录",
        "",
        "下列为原始自动评分，未经事后改分；需结合原回答和实际工具证据复核。",
        "",
    ]
    for r in sorted(rows, key=lambda r: (r["case_id"], r["repeat"], r["mode"])):
        if r["quality"]["status"] == "judge_failed":
            continue
        if r["quality"]["task_success"]:
            continue
        failures += [
            f"## {r['case_id']} / {r['repeat']} / {r['mode']}",
            "",
            r["question"],
            "",
            r["answer"] or f"执行失败：{r.get('error')}",
            "",
            "评分：",
            "",
        ]
        failures += [
            f"- {item['id']}: {item.get('reason', '')}"
            for item in r["quality"].get("criteria", [])
            if not item["passed"]
        ]
        failures += [f"- 无依据事实：{v}" for v in r["quality"].get("unsupported_claims", [])]
        failures += [f"- 事实冲突：{v}" for v in r["quality"].get("contradictions", [])]
        failures += [""]
    (directory / "failures.md").write_text("\n".join(failures), encoding="utf-8")
    with (directory / "trial_metrics.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = [
            "case_id",
            "category",
            "repeat",
            "mode",
            "task_success",
            "criteria_covered",
            "criteria_total",
            "required_tools_covered",
            "required_tools_total",
            "executed_tools",
            "unnecessary_tools",
            "graph_ms",
            "chat_tokens",
            "embedding_tokens",
            "quality_status",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for r in sorted(rows, key=lambda r: (r["case_id"], r["repeat"], r["mode"])):
            writer.writerow(
                {
                    "case_id": r["case_id"],
                    "category": r["category"],
                    "repeat": r["repeat"],
                    "mode": r["mode"],
                    "task_success": r["quality"]["task_success"]
                    if r["quality"]["status"] != "judge_failed"
                    else "",
                    "criteria_covered": r["quality"]["covered"],
                    "criteria_total": r["quality"]["total"],
                    "required_tools_covered": r["tools"]["covered_groups"],
                    "required_tools_total": r["tools"]["required_groups"],
                    "executed_tools": r["tools"]["executed_calls"],
                    "unnecessary_tools": r["tools"]["unnecessary_calls"],
                    "graph_ms": r["graph_ms"],
                    "chat_tokens": r["chat_usage"]["total_tokens"],
                    "embedding_tokens": r["embedding_usage"]["total_tokens"],
                    "quality_status": r["quality"]["status"],
                }
            )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    results = report(args.directory.resolve())
    print(
        json.dumps(
            {"audit": results["audit"], "modes": results["modes"]}, ensure_ascii=False, indent=2
        )
    )
