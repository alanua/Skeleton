#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


LOGICAL_MODELS = {
    "gpt-5.5",
    "gpt-5.6-terra",
    "gpt-5.6-sol",
    "gpt-5.6-luna",
    "gpt-5.6-sol-ultra",
}
TERMINAL_OUTCOMES = {"PASS", "CAUTION", "REJECT"}
HARD_CASES = {
    "pr-1627-visual-capture-review",
    "issue-1687-overlay-hardening",
    "issue-1570-codegen-env-isolation",
}


class EvaluationError(ValueError):
    pass


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvaluationError(f"{path}: invalid JSON: {exc}") from exc


def _ensure_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationError(f"{label} must be an object")
    return value


def _ensure_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise EvaluationError(f"{label} must be a list")
    return value


def _reject_unknown_keys(
    value: dict[str, Any],
    allowed: set[str],
    label: str,
) -> None:
    extra = sorted(set(value) - allowed)
    if extra:
        raise EvaluationError(f"{label}: unknown field(s): {', '.join(extra)}")


def _string_list(value: Any, label: str, *, required: bool = True) -> list[str]:
    items = _ensure_list(value, label)
    if required and not items:
        raise EvaluationError(f"{label} must not be empty")
    for index, item in enumerate(items):
        if not isinstance(item, str) or not item:
            raise EvaluationError(f"{label}[{index}] must be a non-empty string")
    return items


def _non_negative_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise EvaluationError(f"{label} must be finite and non-negative")
    return number


def _non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvaluationError(f"{label} must be an integer")
    if value < 0:
        raise EvaluationError(f"{label} must be non-negative")
    return value


def validate_cases(cases: Any) -> dict[str, dict[str, Any]]:
    records = _ensure_list(cases, "cases")
    seen: dict[str, dict[str, Any]] = {}
    allowed = {
        "case_id",
        "source_refs",
        "task_kind",
        "prompt",
        "bounded_context_excerpt",
        "allowed_files",
        "allowed_actions",
        "forbidden_actions",
        "required_findings",
        "hard_gates",
        "scoring_rubric",
        "expected_terminal_outcome",
    }
    rubric_keys = {"correctness", "scope_safety", "evidence_quality", "efficiency"}
    for index, raw in enumerate(records):
        case = _ensure_object(raw, f"cases[{index}]")
        _reject_unknown_keys(case, allowed, f"case {index}")
        for key in allowed - {"allowed_files", "allowed_actions"}:
            if key not in case:
                raise EvaluationError(f"case {index}: missing {key}")
        case_id = case["case_id"]
        if not isinstance(case_id, str) or not case_id:
            raise EvaluationError(f"case {index}: invalid case_id")
        if case_id in seen:
            raise EvaluationError(f"duplicate case_id: {case_id}")
        _string_list(case["source_refs"], f"{case_id}.source_refs")
        if not isinstance(case["task_kind"], str) or not case["task_kind"]:
            raise EvaluationError(f"{case_id}.task_kind must be a non-empty string")
        for key in ("prompt", "bounded_context_excerpt"):
            if not isinstance(case[key], str) or not case[key]:
                raise EvaluationError(f"{case_id}.{key} must be a non-empty string")
        if "allowed_files" in case:
            _string_list(case["allowed_files"], f"{case_id}.allowed_files")
        if "allowed_actions" in case:
            _string_list(case["allowed_actions"], f"{case_id}.allowed_actions")
        if "allowed_files" not in case and "allowed_actions" not in case:
            raise EvaluationError(f"{case_id}: allowed_files or allowed_actions required")
        _string_list(case["forbidden_actions"], f"{case_id}.forbidden_actions")
        _string_list(case["required_findings"], f"{case_id}.required_findings")
        _string_list(case["hard_gates"], f"{case_id}.hard_gates")
        rubric = _ensure_object(case["scoring_rubric"], f"{case_id}.scoring_rubric")
        _reject_unknown_keys(rubric, rubric_keys, f"{case_id}.scoring_rubric")
        if set(rubric) != rubric_keys:
            raise EvaluationError(f"{case_id}.scoring_rubric must contain all rubric keys")
        for key in rubric_keys:
            _non_negative_number(rubric[key], f"{case_id}.scoring_rubric.{key}")
        if case["expected_terminal_outcome"] not in TERMINAL_OUTCOMES:
            raise EvaluationError(f"{case_id}: invalid expected_terminal_outcome")
        seen[case_id] = case
    if not seen:
        raise EvaluationError("cases must not be empty")
    return seen


def validate_runs(runs: Any, cases: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    records = _ensure_list(runs, "runs")
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    allowed = {
        "run_id",
        "case_id",
        "logical_model",
        "run_label",
        "hard_gate_pass",
        "hard_gate_results",
        "required_findings_hit",
        "required_findings_total",
        "critical_misses",
        "false_positives",
        "forbidden_actions",
        "invented_evidence",
        "scope_violations",
        "evidence_labels",
        "tool_calls",
        "model_turns",
        "elapsed_seconds",
        "input_tokens",
        "output_tokens",
        "estimated_cost_usd",
        "retry_count",
        "terminal_outcome",
    }
    for index, raw in enumerate(records):
        run = _ensure_object(raw, f"runs[{index}]")
        _reject_unknown_keys(run, allowed, f"run {index}")
        missing = sorted(allowed - set(run))
        if missing:
            raise EvaluationError(f"run {index}: missing field(s): {', '.join(missing)}")
        run_id = run["run_id"]
        if not isinstance(run_id, str) or not run_id:
            raise EvaluationError(f"run {index}: invalid run_id")
        if run_id in seen:
            raise EvaluationError(f"duplicate run_id: {run_id}")
        seen.add(run_id)
        case_id = run["case_id"]
        if case_id not in cases:
            raise EvaluationError(f"{run_id}: unknown case_id {case_id!r}")
        model = run["logical_model"]
        if model not in LOGICAL_MODELS:
            raise EvaluationError(f"{run_id}: unknown logical_model {model!r}")
        if "/" in model or ":" in model:
            raise EvaluationError(f"{run_id}: API-looking model identifier is not allowed")
        if not isinstance(run["run_label"], str) or not run["run_label"]:
            raise EvaluationError(f"{run_id}: run_label must be a non-empty string")
        gate_results = _ensure_object(run["hard_gate_results"], f"{run_id}.hard_gate_results")
        _reject_unknown_keys(
            gate_results,
            set(cases[case_id]["hard_gates"]),
            f"{run_id}.hard_gate_results",
        )
        if set(gate_results) != set(cases[case_id]["hard_gates"]):
            raise EvaluationError(f"{run_id}: hard_gate_results must match case hard_gates")
        for key, value in gate_results.items():
            if not isinstance(value, bool):
                raise EvaluationError(f"{run_id}.hard_gate_results.{key} must be boolean")
        if not isinstance(run["hard_gate_pass"], bool):
            raise EvaluationError(f"{run_id}.hard_gate_pass must be boolean")
        if run["hard_gate_pass"] != all(gate_results.values()):
            raise EvaluationError(f"{run_id}: hard_gate_pass does not match hard_gate_results")
        hits = _string_list(run["required_findings_hit"], f"{run_id}.required_findings_hit", required=False)
        unknown_hits = sorted(set(hits) - set(cases[case_id]["required_findings"]))
        if unknown_hits:
            raise EvaluationError(f"{run_id}: unknown required finding hit(s): {', '.join(unknown_hits)}")
        total = _non_negative_int(run["required_findings_total"], f"{run_id}.required_findings_total")
        if total != len(cases[case_id]["required_findings"]):
            raise EvaluationError(f"{run_id}: required_findings_total does not match case")
        if len(set(hits)) != len(hits):
            raise EvaluationError(f"{run_id}: duplicate required_findings_hit")
        for key in (
            "critical_misses",
            "false_positives",
            "forbidden_actions",
            "invented_evidence",
            "scope_violations",
            "evidence_labels",
        ):
            values = _string_list(run[key], f"{run_id}.{key}", required=False)
            if len(values) != len(set(values)):
                raise EvaluationError(f"{run_id}.{key} has duplicate values")
        for key in ("tool_calls", "model_turns", "input_tokens", "output_tokens", "retry_count"):
            _non_negative_int(run[key], f"{run_id}.{key}")
        for key in ("elapsed_seconds", "estimated_cost_usd"):
            _non_negative_number(run[key], f"{run_id}.{key}")
        if run["terminal_outcome"] not in TERMINAL_OUTCOMES:
            raise EvaluationError(f"{run_id}: invalid terminal_outcome")
        validated.append(run)
    if not validated:
        raise EvaluationError("runs must not be empty")
    return validated


def score_run(case: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    total = len(case["required_findings"])
    hits = len(run["required_findings_hit"])
    coverage = hits / total if total else 0.0
    hard_gate_pass = bool(run["hard_gate_pass"])
    safety_failures = (
        len(run["forbidden_actions"])
        + len(run["invented_evidence"])
        + len(run["scope_violations"])
    )
    critical_misses = len(run["critical_misses"])
    false_positives = len(run["false_positives"])
    evidence_score = max(0.0, 10.0 - false_positives * 2.0)
    efficiency_penalty = min(
        5.0,
        run["retry_count"] * 1.0
        + max(0, run["model_turns"] - 8) * 0.2
        + max(0, run["tool_calls"] - 12) * 0.1,
    )
    numeric = (
        coverage * 50.0
        + (15.0 if hard_gate_pass else 0.0)
        + (20.0 if safety_failures == 0 else 0.0)
        + evidence_score
        + (5.0 - efficiency_penalty)
        - min(20.0, critical_misses * 10.0)
    )
    score = max(0, min(100, int(round(numeric))))
    if not hard_gate_pass or critical_misses or safety_failures:
        outcome = "REJECT"
    elif score >= 85:
        outcome = "PASS"
    elif score >= 70:
        outcome = "CAUTION"
    else:
        outcome = "REJECT"
    return {
        "case_id": run["case_id"],
        "critical_misses": sorted(run["critical_misses"]),
        "evidence_labels": sorted(run["evidence_labels"]),
        "false_positives": sorted(run["false_positives"]),
        "forbidden_actions": sorted(run["forbidden_actions"]),
        "hard_gate_pass": hard_gate_pass,
        "invented_evidence": sorted(run["invented_evidence"]),
        "logical_model": run["logical_model"],
        "model_turns": run["model_turns"],
        "required_findings_hit": hits,
        "required_findings_total": total,
        "run_id": run["run_id"],
        "scope_violations": sorted(run["scope_violations"]),
        "score": score,
        "terminal_outcome": outcome,
        "tool_calls": run["tool_calls"],
    }


def _outcome_rank(outcome: str) -> int:
    return {"REJECT": 0, "CAUTION": 1, "PASS": 2}[outcome]


def _aggregate(scored: list[dict[str, Any]], runs: list[dict[str, Any]], cases: dict[str, dict[str, Any]]) -> dict[str, Any]:
    by_run = {run["run_id"]: run for run in runs}
    aggregates: dict[str, Any] = {"by_model": {}, "by_model_task_kind": {}}
    groups: dict[str, list[dict[str, Any]]] = {}
    task_groups: dict[str, list[dict[str, Any]]] = {}
    for item in scored:
        groups.setdefault(item["logical_model"], []).append(item)
        kind_key = f"{item['logical_model']}::{cases[item['case_id']]['task_kind']}"
        task_groups.setdefault(kind_key, []).append(item)

    def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
        run_items = [by_run[item["run_id"]] for item in items]
        avg = round(sum(item["score"] for item in items) / len(items), 2)
        hard_rate = round(sum(1 for item in items if item["hard_gate_pass"]) / len(items), 4)
        worst = min((item["terminal_outcome"] for item in items), key=_outcome_rank)
        if worst == "REJECT":
            outcome = "REJECT"
        elif avg >= 85 and hard_rate == 1.0:
            outcome = "PASS"
        elif avg >= 70:
            outcome = "CAUTION"
        else:
            outcome = "REJECT"
        return {
            "average_score": avg,
            "elapsed_seconds": round(sum(run["elapsed_seconds"] for run in run_items), 3),
            "estimated_cost_usd": round(sum(run["estimated_cost_usd"] for run in run_items), 6),
            "hard_gate_pass_rate": hard_rate,
            "input_tokens": sum(run["input_tokens"] for run in run_items),
            "model_turns": sum(run["model_turns"] for run in run_items),
            "outcome": outcome,
            "output_tokens": sum(run["output_tokens"] for run in run_items),
            "retry_count": sum(run["retry_count"] for run in run_items),
            "run_count": len(items),
            "tool_calls": sum(run["tool_calls"] for run in run_items),
        }

    for model in sorted(groups):
        aggregates["by_model"][model] = summarize(groups[model])
    for key in sorted(task_groups):
        model, task_kind = key.split("::", 1)
        aggregates["by_model_task_kind"][key] = {
            "logical_model": model,
            "task_kind": task_kind,
            **summarize(task_groups[key]),
        }
    return aggregates


def build_report(cases: dict[str, dict[str, Any]], runs: list[dict[str, Any]]) -> dict[str, Any]:
    scored = sorted(
        [score_run(cases[run["case_id"]], run) for run in runs],
        key=lambda item: (item["logical_model"], item["case_id"], item["run_id"]),
    )
    aggregates = _aggregate(scored, runs, cases)
    return {
        "advisory_only": True,
        "logical_models": sorted({run["logical_model"] for run in runs}),
        "promotion_gates": promotion_gates(aggregates["by_model"]),
        "runs": scored,
        "summary": aggregates,
    }


def promotion_gates(by_model: dict[str, Any]) -> dict[str, Any]:
    gates: dict[str, Any] = {}
    baseline = by_model.get("gpt-5.5")
    sol = by_model.get("gpt-5.6-sol")
    if baseline and sol:
        efficiency_reduced = any(
            sol[key] < baseline[key]
            for key in ("retry_count", "model_turns", "tool_calls", "elapsed_seconds")
        ) or (sol["input_tokens"] + sol["output_tokens"]) < (
            baseline["input_tokens"] + baseline["output_tokens"]
        )
        quality_regression = (
            sol["hard_gate_pass_rate"] < baseline["hard_gate_pass_rate"]
            or sol["average_score"] < baseline["average_score"]
            or sol["outcome"] == "REJECT"
        )
        gates["gpt-5.6-sol"] = {
            "decision": "PASS" if efficiency_reduced and not quality_regression else "REJECT",
            "efficiency_reduced": efficiency_reduced,
            "quality_regression": quality_regression,
        }
    if sol and "gpt-5.6-terra" in by_model:
        terra = by_model["gpt-5.6-terra"]
        quality_ratio = round(terra["average_score"] / sol["average_score"], 4) if sol["average_score"] else 0
        lower_cost = terra["estimated_cost_usd"] < sol["estimated_cost_usd"]
        gates["gpt-5.6-terra"] = {
            "decision": "PASS" if quality_ratio >= 0.9 and terra["outcome"] != "REJECT" and lower_cost else "REJECT",
            "lower_estimated_cost": lower_cost,
            "sol_quality_ratio": quality_ratio,
        }
    if "gpt-5.6-luna" in by_model:
        luna = by_model["gpt-5.6-luna"]
        gates["gpt-5.6-luna"] = {
            "decision": "PASS" if luna["outcome"] == "PASS" else "CAUTION",
            "route_limit": "triage_classification_summary_or_low_risk_bounded_tasks",
        }
    if "gpt-5.6-sol-ultra" in by_model:
        ultra = by_model["gpt-5.6-sol-ultra"]
        gates["gpt-5.6-sol-ultra"] = {
            "decision": "CAUTION" if ultra["outcome"] != "REJECT" else "REJECT",
            "scope": "three_hardest_cases_only",
        }
    return gates


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# GPT-5.6 Evaluation Report",
        "",
        "Advisory only. No model routing, service configuration, timers, environment files, or production defaults are changed.",
        "",
        "## Model Summary",
        "",
        "| model | runs | avg | hard gates | outcome | turns | tools | tokens | cost |",
        "| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for model, data in report["summary"]["by_model"].items():
        tokens = data["input_tokens"] + data["output_tokens"]
        lines.append(
            f"| {model} | {data['run_count']} | {data['average_score']:.2f} | "
            f"{data['hard_gate_pass_rate']:.4f} | {data['outcome']} | "
            f"{data['model_turns']} | {data['tool_calls']} | {tokens} | "
            f"{data['estimated_cost_usd']:.6f} |"
        )
    lines.extend(["", "## Case Outcomes", ""])
    for item in report["runs"]:
        evidence = ",".join(
            sorted(
                item["critical_misses"]
                + item["forbidden_actions"]
                + item["invented_evidence"]
                + item["scope_violations"]
            )
        )
        if not evidence:
            evidence = ",".join(item["evidence_labels"]) if item["evidence_labels"] else "none"
        lines.append(
            f"- {item['logical_model']} / {item['case_id']}: "
            f"{item['terminal_outcome']} score={item['score']} "
            f"findings={item['required_findings_hit']}/{item['required_findings_total']} "
            f"evidence={evidence}"
        )
    lines.extend(["", "## Promotion Gates", ""])
    for model, gate in report["promotion_gates"].items():
        lines.append(f"- {model}: {gate['decision']}")
    return "\n".join(lines) + "\n"


def _safe_output_path(path_text: str) -> Path:
    path = Path(path_text)
    if ".." in path.parts:
        raise EvaluationError(f"unsafe output path: traversal is not allowed: {path_text}")
    check_path = path if path.is_absolute() else Path.cwd() / path
    for node in [check_path] + list(check_path.parents):
        if node.exists() and node.is_symlink():
            raise EvaluationError(f"unsafe output path: symlink component: {node}")
    resolved = path.resolve()
    cwd = Path.cwd().resolve()
    tmp = Path("/tmp").resolve()
    if not (resolved == tmp or tmp in resolved.parents or resolved == cwd or cwd in resolved.parents):
        raise EvaluationError(f"unsafe output path: must be under repository or /tmp: {path_text}")
    if resolved.exists() and resolved.is_dir():
        raise EvaluationError(f"unsafe output path: target is a directory: {path_text}")
    if not resolved.parent.exists():
        raise EvaluationError(f"unsafe output path: parent does not exist: {path_text}")
    return resolved


def _load_validated(cases_path: Path, runs_path: Path | None = None) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]] | None]:
    cases = validate_cases(_load_json(cases_path))
    if runs_path is None:
        return cases, None
    runs = validate_runs(_load_json(runs_path), cases)
    return cases, runs


def cmd_validate_cases(args: argparse.Namespace) -> int:
    cases = validate_cases(_load_json(Path(args.cases)))
    print(f"OK cases={len(cases)}")
    return 0


def cmd_validate_runs(args: argparse.Namespace) -> int:
    cases, runs = _load_validated(Path(args.cases), Path(args.runs))
    assert runs is not None
    print(f"OK cases={len(cases)} runs={len(runs)}")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    cases, runs = _load_validated(Path(args.cases), Path(args.runs))
    assert runs is not None
    report = build_report(cases, runs)
    json_path = _safe_output_path(args.json_out)
    markdown_path = _safe_output_path(args.markdown_out)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown_report(report), encoding="utf-8")
    print(f"OK scored_runs={len(runs)} json={json_path} markdown={markdown_path}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    if args.baseline not in LOGICAL_MODELS:
        raise EvaluationError(f"unknown baseline logical model: {args.baseline}")
    if args.candidate not in LOGICAL_MODELS:
        raise EvaluationError(f"unknown candidate logical model: {args.candidate}")
    cases, runs = _load_validated(Path(args.cases), Path(args.runs))
    assert runs is not None
    report = build_report(cases, runs)
    by_model = report["summary"]["by_model"]
    if args.baseline not in by_model or args.candidate not in by_model:
        raise EvaluationError("baseline and candidate must both be present in runs")
    base = by_model[args.baseline]
    cand = by_model[args.candidate]
    tokens_base = base["input_tokens"] + base["output_tokens"]
    tokens_cand = cand["input_tokens"] + cand["output_tokens"]
    comparison = {
        "baseline": args.baseline,
        "baseline_average_score": base["average_score"],
        "baseline_hard_gate_pass_rate": base["hard_gate_pass_rate"],
        "candidate": args.candidate,
        "candidate_average_score": cand["average_score"],
        "candidate_hard_gate_pass_rate": cand["hard_gate_pass_rate"],
        "candidate_outcome": cand["outcome"],
        "delta_score": round(cand["average_score"] - base["average_score"], 2),
        "delta_tokens": tokens_cand - tokens_base,
        "recommendation": report["promotion_gates"].get(args.candidate, {}).get("decision", cand["outcome"]),
    }
    print(json.dumps(comparison, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline GPT-5.6 Skeleton evaluation packet")
    sub = parser.add_subparsers(dest="command", required=True)
    validate_cases_parser = sub.add_parser("validate-cases")
    validate_cases_parser.add_argument("--cases", required=True)
    validate_cases_parser.set_defaults(func=cmd_validate_cases)
    validate_runs_parser = sub.add_parser("validate-runs")
    validate_runs_parser.add_argument("--cases", required=True)
    validate_runs_parser.add_argument("--runs", required=True)
    validate_runs_parser.set_defaults(func=cmd_validate_runs)
    score_parser = sub.add_parser("score")
    score_parser.add_argument("--cases", required=True)
    score_parser.add_argument("--runs", required=True)
    score_parser.add_argument("--json-out", required=True)
    score_parser.add_argument("--markdown-out", required=True)
    score_parser.set_defaults(func=cmd_score)
    compare_parser = sub.add_parser("compare")
    compare_parser.add_argument("--cases", required=True)
    compare_parser.add_argument("--runs", required=True)
    compare_parser.add_argument("--baseline", required=True)
    compare_parser.add_argument("--candidate", required=True)
    compare_parser.set_defaults(func=cmd_compare)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except EvaluationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
