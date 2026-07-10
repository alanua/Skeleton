from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scripts import gpt_5_6_evaluation as evaluation


ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "tests" / "fixtures" / "gpt_5_6_eval" / "cases.json"
RUNS_PATH = ROOT / "tests" / "fixtures" / "gpt_5_6_eval" / "sample_runs.json"
CASE_SCHEMA_PATH = ROOT / "schemas" / "gpt_5_6_eval_case.schema.json"
RUN_SCHEMA_PATH = ROOT / "schemas" / "gpt_5_6_eval_run.schema.json"
SCRIPT_PATH = ROOT / "scripts" / "gpt_5_6_evaluation.py"


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture()
def cases() -> dict[str, dict[str, object]]:
    return evaluation.validate_cases(load_json(CASES_PATH))


@pytest.fixture()
def runs(cases: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    return evaluation.validate_runs(load_json(RUNS_PATH), cases)


def test_schemas_and_bundled_fixtures_validate() -> None:
    case_schema = load_json(CASE_SCHEMA_PATH)
    run_schema = load_json(RUN_SCHEMA_PATH)
    Draft202012Validator.check_schema(case_schema)
    Draft202012Validator.check_schema(run_schema)
    Draft202012Validator(case_schema).validate(load_json(CASES_PATH))
    Draft202012Validator(run_schema).validate(load_json(RUNS_PATH))
    evaluation.validate_cases(load_json(CASES_PATH))
    cases = evaluation.validate_cases(load_json(CASES_PATH))
    evaluation.validate_runs(load_json(RUNS_PATH), cases)


def test_deterministic_score_and_report_output(
    tmp_path: Path,
    cases: dict[str, dict[str, object]],
    runs: list[dict[str, object]],
) -> None:
    report_a = evaluation.build_report(cases, runs)
    report_b = evaluation.build_report(cases, runs)
    assert json.dumps(report_a, sort_keys=True) == json.dumps(report_b, sort_keys=True)
    assert evaluation.markdown_report(report_a) == evaluation.markdown_report(report_b)

    json_a = tmp_path / "a.json"
    md_a = tmp_path / "a.md"
    json_b = tmp_path / "b.json"
    md_b = tmp_path / "b.md"
    assert evaluation.main(
        [
            "score",
            "--cases",
            str(CASES_PATH),
            "--runs",
            str(RUNS_PATH),
            "--json-out",
            str(json_a),
            "--markdown-out",
            str(md_a),
        ]
    ) == 0
    assert evaluation.main(
        [
            "score",
            "--cases",
            str(CASES_PATH),
            "--runs",
            str(RUNS_PATH),
            "--json-out",
            str(json_b),
            "--markdown-out",
            str(md_b),
        ]
    ) == 0
    assert json.loads(json_a.read_text(encoding="utf-8")) == json.loads(
        json_b.read_text(encoding="utf-8")
    )
    assert md_a.read_text(encoding="utf-8") == md_b.read_text(encoding="utf-8")


def test_every_hard_gate_failure_becomes_reject(
    cases: dict[str, dict[str, object]],
    runs: list[dict[str, object]],
) -> None:
    candidate = copy.deepcopy(runs[0])
    first_gate = next(iter(candidate["hard_gate_results"]))
    candidate["hard_gate_results"][first_gate] = False
    candidate["hard_gate_pass"] = False

    scored = evaluation.score_run(cases[candidate["case_id"]], candidate)

    assert scored["terminal_outcome"] == "REJECT"
    assert scored["score"] >= 70


@pytest.mark.parametrize(
    ("field", "label"),
    [
        ("critical_misses", "critical_label"),
        ("invented_evidence", "invented_label"),
        ("forbidden_actions", "forbidden_label"),
        ("scope_violations", "scope_label"),
    ],
)
def test_safety_and_critical_failures_cannot_be_offset_by_efficiency(
    cases: dict[str, dict[str, object]],
    runs: list[dict[str, object]],
    field: str,
    label: str,
) -> None:
    candidate = copy.deepcopy(runs[0])
    candidate.update(
        {
            field: [label],
            "tool_calls": 0,
            "model_turns": 0,
            "elapsed_seconds": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_usd": 0,
            "retry_count": 0,
        }
    )

    scored = evaluation.score_run(cases[candidate["case_id"]], candidate)

    assert scored["terminal_outcome"] == "REJECT"


@pytest.mark.parametrize("model", ["gpt-5.6", "openai/gpt-5.6-sol", "gpt-5.6-sol:2026"])
def test_unknown_logical_model_or_api_looking_identifier_is_rejected(
    cases: dict[str, dict[str, object]],
    runs: list[dict[str, object]],
    model: str,
) -> None:
    candidate = copy.deepcopy(runs)
    candidate[0]["logical_model"] = model

    with pytest.raises(evaluation.EvaluationError):
        evaluation.validate_runs(candidate, cases)


def test_duplicate_run_ids_and_mismatched_case_refs_are_rejected(
    cases: dict[str, dict[str, object]],
    runs: list[dict[str, object]],
) -> None:
    duplicate = copy.deepcopy(runs)
    duplicate[1]["run_id"] = duplicate[0]["run_id"]
    with pytest.raises(evaluation.EvaluationError):
        evaluation.validate_runs(duplicate, cases)

    mismatched = copy.deepcopy(runs)
    mismatched[0]["case_id"] = "missing-case"
    with pytest.raises(evaluation.EvaluationError):
        evaluation.validate_runs(mismatched, cases)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tool_calls", -1),
        ("model_turns", -1),
        ("elapsed_seconds", -0.1),
        ("input_tokens", -1),
        ("output_tokens", -1),
        ("estimated_cost_usd", -0.01),
        ("retry_count", -1),
        ("elapsed_seconds", math.inf),
        ("estimated_cost_usd", math.nan),
    ],
)
def test_negative_and_non_finite_metrics_are_rejected(
    cases: dict[str, dict[str, object]],
    runs: list[dict[str, object]],
    field: str,
    value: object,
) -> None:
    candidate = copy.deepcopy(runs)
    candidate[0][field] = value

    with pytest.raises(evaluation.EvaluationError):
        evaluation.validate_runs(candidate, cases)


def test_unsafe_absolute_traversal_and_symlink_output_targets_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(evaluation.EvaluationError):
        evaluation._safe_output_path("/etc/gpt-5-6-eval.json")
    with pytest.raises(evaluation.EvaluationError):
        evaluation._safe_output_path("../gpt-5-6-eval.json")

    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(evaluation.EvaluationError):
        evaluation._safe_output_path(str(link))


def test_report_excludes_fixture_context_beyond_ids_aggregates_and_evidence_labels(
    cases: dict[str, dict[str, object]],
    runs: list[dict[str, object]],
) -> None:
    markdown = evaluation.markdown_report(evaluation.build_report(cases, runs))

    assert "The task metadata used a non-matching heading" not in markdown
    assert "leaked parent variables" not in markdown
    assert "placeholder adapter" not in markdown
    assert "issue-1574-allowed-files-parser" in markdown
    assert "bounded_excerpt" in markdown


def test_no_network_model_invocation_credential_lookup_or_production_mutation_exists() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden_snippets = [
        "import subprocess",
        "from subprocess",
        "requests.",
        "urllib.request",
        "httpx.",
        "openai",
        "anthropic",
        "google.generativeai",
        "os.environ",
        "GITHUB_TOKEN",
        "SKELETON_HOME_EDGE_01_",
        "PROVIDER_ROUTING",
    ]

    for snippet in forbidden_snippets:
        assert snippet not in source


def test_compare_contract_returns_candidate_summary(capsys: pytest.CaptureFixture[str]) -> None:
    assert evaluation.main(
        [
            "compare",
            "--cases",
            str(CASES_PATH),
            "--runs",
            str(RUNS_PATH),
            "--baseline",
            "gpt-5.5",
            "--candidate",
            "gpt-5.6-sol",
        ]
    ) == 0
    captured = json.loads(capsys.readouterr().out)

    assert captured["baseline"] == "gpt-5.5"
    assert captured["candidate"] == "gpt-5.6-sol"
    assert captured["recommendation"] == "PASS"
