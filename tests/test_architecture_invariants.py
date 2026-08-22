from __future__ import annotations

from core.architecture_invariants import (
    ArchitectureImpact,
    ArchitectureInvariantEvidence,
    evaluate_architecture_invariants,
)
from core.quality_evidence import ReviewEvidence


HEAD = "b" * 40


def green_architecture_evidence() -> ArchitectureInvariantEvidence:
    return ArchitectureInvariantEvidence(
        invariants_checked=True,
        immutable_invariants_preserved=True,
        dependency_boundaries_preserved=True,
        capability_surface_reviewed=True,
        bound_head_sha=HEAD,
    )


def green_review() -> ReviewEvidence:
    return ReviewEvidence(
        independent_review=True,
        adversarial_review=True,
        protected_review_required=True,
        bound_head_sha=HEAD,
    )


def test_yellow_task_needs_architecture_and_independent_review_evidence() -> None:
    decision = evaluate_architecture_invariants(
        touched_files=("core/task_quality_gate.py",),
        declared_risk="yellow",
        current_head_sha=HEAD,
    )

    assert not decision.allowed
    assert decision.impact is ArchitectureImpact.YELLOW
    assert "ARCHITECTURE_INVARIANT_EVIDENCE_REQUIRED" in decision.reason_codes
    assert "INDEPENDENT_REVIEW_EVIDENCE_REQUIRED" in decision.reason_codes


def test_protected_target_requires_protected_review_flag() -> None:
    decision = evaluate_architecture_invariants(
        touched_files=("core/action_gate.py",),
        declared_risk="green",
        evidence=green_architecture_evidence(),
        review=ReviewEvidence(
            independent_review=True,
            adversarial_review=True,
            protected_review_required=False,
            bound_head_sha=HEAD,
        ),
        current_head_sha=HEAD,
    )

    assert not decision.allowed
    assert decision.impact is ArchitectureImpact.PROTECTED
    assert "PROTECTED_REVIEW_REQUIRED" in decision.reason_codes


def test_exact_sha_change_invalidates_architecture_and_review_evidence() -> None:
    decision = evaluate_architecture_invariants(
        touched_files=("core/task_quality_gate.py",),
        declared_risk="yellow",
        evidence=green_architecture_evidence(),
        review=green_review(),
        current_head_sha="c" * 40,
    )

    assert "ARCHITECTURE_EVIDENCE_SHA_MISMATCH" in decision.reason_codes
    assert "REVIEW_SHA_MISMATCH" in decision.reason_codes


def test_self_modifying_policy_diff_is_blocked_without_explicit_flags() -> None:
    decision = evaluate_architecture_invariants(
        touched_files=("OPERATOR_RULES.yaml",),
        declared_risk="protected",
        evidence=green_architecture_evidence(),
        review=green_review(),
        current_head_sha=HEAD,
    )

    assert "SELF_MODIFYING_POLICY_INVARIANT_BLOCKED" in decision.reason_codes


def test_self_modifying_policy_diff_can_pass_with_policy_change_and_protected_review() -> None:
    evidence = ArchitectureInvariantEvidence(
        invariants_checked=True,
        immutable_invariants_preserved=True,
        dependency_boundaries_preserved=True,
        capability_surface_reviewed=True,
        policy_change=True,
        protected_review_required=True,
        bound_head_sha=HEAD,
    )
    decision = evaluate_architecture_invariants(
        touched_files=("OPERATOR_RULES.yaml",),
        declared_risk="protected",
        evidence=evidence,
        review=green_review(),
        current_head_sha=HEAD,
    )

    assert decision.allowed
    assert decision.public_receipt()["protected_target_count"] == 1
    assert "OPERATOR_RULES.yaml" not in str(decision.public_receipt())
