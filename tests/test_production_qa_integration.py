from __future__ import annotations

import pytest

from core.architecture_invariants import evaluate_architecture_invariants
from core.capability_diff import observed_diff_impact
from core.quality_evidence import production_qa_receipt
from core.runner_codegen_router import production_qa_route_receipt, validate_task_spec_before_codegen
from core.task_quality_gate import normalize_task_spec


def _task(capabilities: list[str] | None = None) -> str:
    return f"""
schema: skeleton.runner_task.v1
repo: alanua/Skeleton
branch: runner/production-qa
task_kind: code_generation
payload:
  operation: integrate
requested_capabilities: {capabilities or ["repository_read", "repository_write", "test_execution"]}
allowed_files:
  - core/review_gate.py
  - tests/test_review_gate.py
forbidden_actions:
  - no merge
validation:
  - python3 -m pytest -q
expected_output:
  - NO MERGE
privacy_boundary: PUBLIC_SAFE_CONTROL_AND_EVIDENCE_METADATA_ONLY
idempotency_key: production-qa-phase4-runner-reviewer-integration-after-3177-v3
"""


def test_synthetic_task_to_observed_qa_state_machine_never_runtime_proven() -> None:
    receipt = production_qa_route_receipt(
        task_content=_task(["repository_read"]),
        head_sha="a" * 40,
        changed_files=("tests/test_review_gate.py",),
        author_identity="runner-codegen",
        declared_tests_green=True,
        declared_impact="green",
    )

    assert receipt.state == "PRODUCTION_READY"
    assert receipt.next_action == "EXACT_HEAD_OPERATOR_REVIEW_THEN_PHASE5_RUNTIME_PROOF"
    assert receipt.state != "RUNTIME_PROVEN"


def test_read_only_route_rejects_privileged_mutation_despite_green_tests() -> None:
    task = normalize_task_spec(_task(["repository_read"]))
    observed = observed_diff_impact(("core/action_gate.py",))
    invariant = evaluate_architecture_invariants(
        task,
        observed,
        evidence_labels=("tests_green",),
    )

    assert not invariant.passed
    assert "READ_ONLY_ROUTE_MUTATED_PROTECTED_OR_PRODUCTION" in invariant.failures


def test_mock_only_missing_production_capability_rejected_despite_green_tests() -> None:
    task = normalize_task_spec(_task())
    observed = observed_diff_impact(("tests/fixtures/mock_only.json",))
    invariant = evaluate_architecture_invariants(
        task,
        observed,
        evidence_labels=("tests_green",),
    )

    assert not invariant.passed
    assert "MOCK_ONLY_PROOF_MISSING_PRODUCTION_CAPABILITY" in invariant.failures


def test_claim_proof_schema_regressions_remain_rejected() -> None:
    task = normalize_task_spec(_task())
    observed = observed_diff_impact(("tests/test_review_gate.py",))
    invariant = evaluate_architecture_invariants(
        task,
        observed,
        evidence_labels=("schema_regression", "proof_regression"),
    )

    assert not invariant.passed
    assert "CLAIM_PROOF_SCHEMA_REGRESSION" in invariant.failures


def test_no_state_becomes_runtime_proven_here() -> None:
    with pytest.raises(ValueError, match="RUNTIME_PROVEN_NOT_MATERIALIZED_IN_PHASE4"):
        production_qa_receipt(
            exact_head_sha="a" * 40,
            changed_files=(),
            impact_level=validate_task_spec_before_codegen(_task()).predicted_impact.level,
            reasons=(),
            state="RUNTIME_PROVEN",
        )
