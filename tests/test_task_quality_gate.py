from __future__ import annotations

import pytest

from core.task_quality_gate import (
    PRIVATE_PROTECTED,
    PUBLIC_REVIEW_ALLOWED,
    PUBLIC_SAFE,
    PROTECTED_REVIEW_REQUIRED,
    REVIEW_RELEVANT,
    TaskQualityGateError,
    evaluate_task_quality,
    normalize_privacy_boundary,
    normalize_risk,
    protected_surfaces,
)


BASE_SHA = "47320dab7740b6c26d006e1b6e3e8d23cd7bcca5"


def live_3197_fixture() -> dict[str, object]:
    return {
        "schema": "skeleton.runner_task.v1",
        "repo": "alanua/Skeleton",
        "base": "main",
        "base_sha": BASE_SHA,
        "branch": "runner/live-3197",
        "task_kind": "code_generation",
        "payload": {
            "operation": "claim_side_validation",
            "architecture_required": True,
            "production_contract_required": True,
        },
        "requested_capabilities": ["repository_read", "repository_write", "test_execution"],
        "allowed_files": ["core/task_quality_gate.py"],
        "forbidden_actions": ["no runtime mutation"],
        "validation": ["python3 -m pytest tests/test_task_quality_gate.py -q"],
        "expected_output": ["NO MERGE"],
        "privacy": "PRIVATE_PRIVILEGE_STATE_LOCAL_ONLY / PUBLIC_SAFE_HASH_STATUS_ONLY",
        "idempotency": "live-3197-fixture",
        "risk": "yellow",
    }


def live_3166_fixture() -> dict[str, object]:
    return {
        "schema": "skeleton.runner_task.v1",
        "repo": "alanua/Skeleton",
        "project": "skeleton",
        "base": "main",
        "base_sha": BASE_SHA,
        "branch": "runner/live-3166",
        "task_kind": "code_generation",
        "payload": {"operation": "cross_project_contract"},
        "requested_capabilities": ["repository_read", "repository_write", "test_execution"],
        "allowed_files": ["core/quality_evidence.py", "tests/test_quality_evidence.py"],
        "forbidden_actions": ["no proof authority"],
        "validation": ["full python3 -m pytest -q"],
        "required_tests": ["tests/test_quality_evidence.py"],
        "expected_output": ["NO MERGE"],
        "privacy": "PUBLIC_SAFE_CODE_AND_SYNTHETIC_TESTS_ONLY",
        "idempotency": "live-3166-fixture",
        "risk": "green",
    }


def test_live_3197_claim_side_fixture_accepts_yellow_private_composite_without_raw_exposure() -> None:
    decision = evaluate_task_quality(live_3197_fixture())

    assert decision.status == "protected_review_required"
    assert decision.task.risk.canonical == "yellow"
    assert decision.task.risk.review_class == REVIEW_RELEVANT
    assert decision.task.privacy_boundary.privacy_class == PRIVATE_PROTECTED
    assert decision.task.privacy_boundary.public_safe_portions == (
        "PUBLIC_SAFE_HASH_STATUS_ONLY",
    )
    assert "PRIVATE_PRIVILEGE_STATE_LOCAL_ONLY" not in repr(decision.to_mapping())


def test_live_3166_cross_project_fixture_preserves_supported_shape_without_semantic_loss() -> None:
    task = evaluate_task_quality(live_3166_fixture()).task
    mapped = task.to_mapping()

    for field in (
        "repo",
        "project",
        "base",
        "base_sha",
        "branch",
        "task_kind",
        "payload",
        "requested_capabilities",
        "allowed_files",
        "forbidden_actions",
        "validation",
        "required_tests",
        "expected_output",
        "idempotency_key",
    ):
        assert mapped[field]
    assert mapped["project"] == "skeleton"
    assert mapped["extensions"]["project"] == "skeleton"
    assert mapped["extensions"]["required_tests"] == ["tests/test_quality_evidence.py"]
    assert task.privacy_boundary.privacy_class == PUBLIC_SAFE
    assert task.risk.review_class == PUBLIC_REVIEW_ALLOWED


@pytest.mark.parametrize(
    "boundary",
    [
        "PUBLIC_SAFE_CODE_AND_SYNTHETIC_TESTS_ONLY",
        "PUBLIC_SAFE_SOURCE_AND_SYNTHETIC_TESTS_ONLY",
    ],
)
def test_live_public_safe_code_and_source_boundaries_are_public_safe(boundary: str) -> None:
    normalized = normalize_privacy_boundary(boundary)

    assert normalized.privacy_class == PUBLIC_SAFE
    assert not normalized.protected


@pytest.mark.parametrize(
    ("alias", "canonical", "review_class", "protected"),
    [
        ("green", "green", PUBLIC_REVIEW_ALLOWED, False),
        ("low", "green", PUBLIC_REVIEW_ALLOWED, False),
        ("yellow", "yellow", REVIEW_RELEVANT, False),
        ("medium", "yellow", REVIEW_RELEVANT, False),
        ("red", "red", PROTECTED_REVIEW_REQUIRED, True),
        ("high", "red", PROTECTED_REVIEW_REQUIRED, True),
        ("critical", "critical", PROTECTED_REVIEW_REQUIRED, True),
        ("protected", "critical", PROTECTED_REVIEW_REQUIRED, True),
    ],
)
def test_risk_aliases_normalize_deterministically(
    alias: str,
    canonical: str,
    review_class: str,
    protected: bool,
) -> None:
    risk = normalize_risk(alias)

    assert risk.canonical == canonical
    assert risk.review_class == review_class
    assert risk.protected is protected


def test_green_low_public_safe_scope_can_remain_public_review_allowed() -> None:
    fixture = live_3166_fixture()
    fixture["risk"] = "low"
    fixture["privacy"] = "PUBLIC_SAFE_SOURCE_AND_SYNTHETIC_TESTS_ONLY"

    decision = evaluate_task_quality(fixture)

    assert decision.status == "public_review_allowed"
    assert decision.allowed


@pytest.mark.parametrize(
    "path",
    [
        "BOOT_MANIFEST.yaml",
        "PROJECT_TREE.yaml",
        "OPERATOR_RULES.yaml",
        "CAPABILITY_REGISTRY.yaml",
        "scripts/runner_poll_github_tasks.py",
        "core/gate_engine.py",
        "core/action_gate.py",
        ".github/workflows/runner.yml",
        "adapters/chatgpt/SYSTEM_PROMPT.md",
        "secrets/example.txt",
        "deploy/production.yaml",
        "server/runtime.py",
        "finance/report.py",
        "legal/policy.md",
        "governance/contract.yaml",
        "Runner_core/engine.py",
        "adapter_boundaries/contract.md",
    ],
)
def test_every_canonical_protected_surface_exact_and_glob_stays_green(path: str) -> None:
    assert protected_surfaces([path]) == (path,)


def test_caller_exact_proof_states_architecture_green_and_runtime_proven_reject() -> None:
    fixture = live_3166_fixture()
    fixture["payload"] = {
        "architecture_state": "ARCHITECTURE_GREEN",
        "runtime_state": "RUNTIME_PROVEN",
    }

    decision = evaluate_task_quality(fixture)

    assert decision.protected
    assert "CALLER_PROOF_REJECTED" in decision.reason_codes
    assert decision.evidence.rejected_states == ("ARCHITECTURE_GREEN", "RUNTIME_PROVEN")


def test_phase1_does_not_materialize_observed_diff_touched_files_or_runtime_proof() -> None:
    fixture = live_3166_fixture()
    fixture["payload"] = {
        "observed_diff_impact": {"files": ["core/task_quality_gate.py"]},
        "touched_files": ["core/task_quality_gate.py"],
        "runtime_proof": {"state": "RUNTIME_PROVEN"},
    }

    mapped = evaluate_task_quality(fixture).to_mapping()

    assert mapped["evidence"]["runtime_status"] == "RUNTIME_REVIEW_UNREACHED"
    assert mapped["evidence"]["observed_diff_status"] == "OBSERVED_DIFF_UNREACHED"
    assert "observed_diff_impact" not in mapped
    assert "touched_files" not in mapped
    assert "runtime_proof" not in mapped


def test_alias_disagreement_and_unknown_unsupported_semantic_fields_fail_closed() -> None:
    fixture = live_3166_fixture()
    fixture["risk_level"] = "red"
    with pytest.raises(TaskQualityGateError, match="risk and risk_level disagree"):
        evaluate_task_quality(fixture)

    fixture = live_3166_fixture()
    fixture["caller_production_receipt"] = {"state": "green"}
    with pytest.raises(TaskQualityGateError) as exc:
        evaluate_task_quality(fixture)
    assert exc.value.reason_code == "UNKNOWN_UNSUPPORTED_FIELD"


def test_unknown_invented_boundary_fails_closed() -> None:
    fixture = live_3166_fixture()
    fixture["privacy"] = "PUBLIC_SAFE_RUNNER_PHASE1_LOW_ONLY"

    with pytest.raises(TaskQualityGateError) as exc:
        evaluate_task_quality(fixture)

    assert exc.value.reason_code == "UNKNOWN_PRIVACY_BOUNDARY"


def test_regression_3206_low_invented_boundary_substitution_is_not_live_compat_evidence() -> None:
    fixture = live_3197_fixture()
    fixture["risk"] = "LOW"
    fixture["privacy"] = "INVENTED_PUBLIC_SAFE_BOUNDARY_ONLY"

    with pytest.raises(TaskQualityGateError) as exc:
        evaluate_task_quality(fixture)

    assert exc.value.reason_code == "UNKNOWN_PRIVACY_BOUNDARY"
