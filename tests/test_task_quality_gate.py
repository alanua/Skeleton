from __future__ import annotations

import pytest

from core.quality_evidence import QualityEvidenceError, TaskSpec
from core.task_quality_gate import classify_scope, evaluate_task_quality_gate


def claims(**overrides):
    base = {
        "repo": "alanua/Skeleton",
        "idempotency_key": "validate-pr-branch:alanua/Skeleton:pr-3181:47320dab",
        "privacy_boundary": "PUBLIC_SAFE_POLICY_METADATA_ONLY",
        "allowed_files": ["core/task_quality_gate.py"],
        "risk": "low",
        "protected_intent": False,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "scope",
    [
        "scripts/runner_poll_github_tasks.py",
        ".github/workflows/**",
        "core/gate_engine.py",
    ],
)
def test_public_safe_privacy_protected_file_scope_requires_protected_review(scope) -> None:
    decision = evaluate_task_quality_gate(claims(allowed_files=[scope], risk="low"))

    assert decision.protected_review_required is True
    assert "PROTECTED_DECLARED_SCOPE" in decision.reason_codes


def test_public_safe_explicit_protected_intent_requires_review_even_with_low_risk() -> None:
    decision = evaluate_task_quality_gate(
        claims(
            allowed_files=["tests/**"],
            protected_intent=True,
            risk="green",
        )
    )

    assert decision.protected_review_required is True
    assert "EXPLICIT_PROTECTED_INTENT" in decision.reason_codes


def test_private_or_composite_boundary_requires_review_for_ordinary_scope() -> None:
    decision = evaluate_task_quality_gate(
        claims(privacy_boundary="COMPOSITE_PRIVATE_PUBLIC_SAFE", allowed_files=["tests/**"])
    )

    assert decision.protected_review_required is True
    assert "PRIVATE_OR_COMPOSITE_PRIVACY_BOUNDARY" in decision.reason_codes


@pytest.mark.parametrize("risk", ["high", "critical", "protected"])
def test_high_critical_or_configured_protected_risk_requires_review(risk) -> None:
    decision = evaluate_task_quality_gate(
        claims(allowed_files=["tests/**"], risk=risk)
    )

    assert decision.protected_review_required is True
    assert "PROTECTED_RISK" in decision.reason_codes


def test_lower_claims_cannot_downgrade_stronger_protected_scope_signal() -> None:
    decision = evaluate_task_quality_gate(
        claims(
            privacy_boundary="PUBLIC_SAFE_POLICY_METADATA_ONLY",
            allowed_files=["core/gate_engine.py"],
            risk="green",
            protected_intent=False,
        )
    )

    assert decision.protected_review_required is True
    assert "PROTECTED_DECLARED_SCOPE" in decision.reason_codes


def test_benign_public_safe_low_risk_ordinary_scope_can_remain_public_review_allowed() -> None:
    decision = evaluate_task_quality_gate(
        claims(allowed_files=["tests/**"], risk="green")
    )

    assert decision.protected_review_required is False
    assert decision.public_review_allowed is True
    assert decision.status == "allowed"


def test_architecture_required_cannot_be_satisfied_by_caller_bool_string_list_or_ids() -> None:
    decision = evaluate_task_quality_gate(
        claims(
            allowed_files=["tests/**"],
            architecture_reviewed=True,
            architecture_status="approved",
            reviewers=["architecture"],
            invariant_ids=["ARCHITECTURE_APPROVED"],
        ),
        architecture_required=True,
    )

    assert decision.protected_review_required is True
    assert "ARCHITECTURE_REVIEW_REQUIRED" in decision.reason_codes


def test_architecture_optional_absence_does_not_block_without_receipt() -> None:
    decision = evaluate_task_quality_gate(claims(allowed_files=["tests/**"]))

    assert "ARCHITECTURE_REVIEW_REQUIRED" not in decision.reason_codes
    assert decision.protected_review_required is False


def test_production_contract_optional_absence_does_not_block_without_receipt() -> None:
    decision = evaluate_task_quality_gate(claims(allowed_files=["tests/**"]))

    assert "PRODUCTION_CONTRACT_REVIEW_REQUIRED" not in decision.reason_codes
    assert decision.protected_review_required is False


def test_head_movement_invalidates_head_bound_evidence_and_requires_review() -> None:
    decision = evaluate_task_quality_gate(
        claims(
            allowed_files=["tests/**"],
            evidence_receipts=[
                {
                    "evidence_type": "unit_tests",
                    "state": "HEAD_BOUND",
                    "head_sha": "a" * 40,
                }
            ],
        ),
        current_head_sha="b" * 40,
    )

    assert decision.protected_review_required is True
    assert "HEAD_BOUND_EVIDENCE_INVALIDATED" in decision.reason_codes
    assert decision.task_spec.evidence_receipts[0].state == "INVALIDATED"


def test_declared_scope_classification_is_deterministic_for_protected_and_ordinary_globs() -> None:
    assert classify_scope(".github/workflows/**").protected is True
    assert classify_scope("tests/**").protected is False
    assert classify_scope("home_edge/generative_visuals/**").protected is False


def test_scope_rejections_are_exposed_from_gate_surface() -> None:
    with pytest.raises(QualityEvidenceError):
        evaluate_task_quality_gate(claims(allowed_files=["/etc/passwd"]))


def test_public_mapping_has_no_touched_files_or_observed_diff_impact() -> None:
    decision = evaluate_task_quality_gate(
        claims(
            allowed_files=["tests/**"],
            touched_files=["core/gate_engine.py"],
            ObservedDiffImpact={"touched_files": ["core/gate_engine.py"]},
        )
    )

    public = decision.public_mapping()
    assert "touched_files" not in repr(public)
    assert "ObservedDiffImpact" not in repr(public)


def test_task_spec_can_be_passed_directly() -> None:
    spec = TaskSpec.from_claims(claims(allowed_files=["tests/**"]))
    decision = evaluate_task_quality_gate(spec)

    assert decision.allowed is True
