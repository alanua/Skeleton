from __future__ import annotations

from core.capability_diff import observed_diff_impact
from core.claim_reconciliation import reconcile_claims
from core.task_quality_gate import normalize_task_spec


def test_declared_green_actual_protected_mutating_diff_escalates() -> None:
    task = normalize_task_spec(
        {
            "schema": "skeleton.runner_task.v1",
            "repo": "alanua/Skeleton",
            "branch": "runner/x",
            "task_kind": "code_generation",
            "payload": {"operation": "x"},
            "requested_capabilities": ["repository_read", "repository_write"],
            "allowed_files": ["tests/test_claim_reconciliation.py"],
            "forbidden_actions": [],
            "validation": [],
            "expected_output": [],
            "privacy_boundary": "PUBLIC_SAFE_REPOSITORY_ONLY",
            "idempotency_key": "x",
        }
    )
    observed = observed_diff_impact(["CAPABILITY_REGISTRY.yaml"])

    result = reconcile_claims(
        task.predicted_impact,
        observed,
        declared_tests_green=True,
        declared_impact="green",
    )

    assert not result.accepted
    assert result.deterministic_failure
    assert result.level.value == "protected"
