from __future__ import annotations

from core.architecture_invariants import InvariantEvaluation
from core.capability_model import ImpactLevel
from core.claim_reconciliation import ClaimReconciliation
from core.evidence_authenticity import ReviewerReceipt
from core.quality_evidence import NEEDS_OPERATOR, NEEDS_REVIEW, PRODUCTION_READY, REJECTED
from core.review_gate import route_adversarial_review


def _reconciliation(level: ImpactLevel) -> ClaimReconciliation:
    return ClaimReconciliation(True, level, (), False)


def _reviewer(identity: str = "reviewer-b") -> ReviewerReceipt:
    return ReviewerReceipt(
        reviewer_identity=identity,
        head_sha="a" * 40,
        bounded_findings=("checked exact head",),
        issued_at="2026-08-22T00:00:00+00:00",
    )


def test_green_benign_change_passes_without_llm_review() -> None:
    decision = route_adversarial_review(
        _reconciliation(ImpactLevel.GREEN),
        InvariantEvaluation(True, ()),
        author_identity="author-a",
        head_sha="a" * 40,
    )

    assert decision.state == PRODUCTION_READY
    assert decision.required_reviewer_receipts == 0


def test_yellow_requires_one_independent_reviewer_receipt() -> None:
    pending = route_adversarial_review(
        _reconciliation(ImpactLevel.YELLOW),
        InvariantEvaluation(True, ()),
        author_identity="author-a",
        head_sha="a" * 40,
    )
    accepted = route_adversarial_review(
        _reconciliation(ImpactLevel.YELLOW),
        InvariantEvaluation(True, ()),
        author_identity="author-a",
        head_sha="a" * 40,
        reviewer_receipts=(_reviewer(),),
    )

    assert pending.state == NEEDS_REVIEW
    assert accepted.state == PRODUCTION_READY


def test_protected_requires_quorum_plus_operator_gate() -> None:
    decision = route_adversarial_review(
        _reconciliation(ImpactLevel.PROTECTED),
        InvariantEvaluation(True, ()),
        author_identity="author-a",
        head_sha="a" * 40,
        reviewer_receipts=(_reviewer(), _reviewer("reviewer-c")),
        configured_quorum=2,
    )

    assert decision.state == NEEDS_OPERATOR
    assert decision.operator_required


def test_disagreement_escalates_and_deterministic_fail_cannot_be_overridden() -> None:
    disagreement = route_adversarial_review(
        _reconciliation(ImpactLevel.YELLOW),
        InvariantEvaluation(True, ()),
        author_identity="author-a",
        head_sha="a" * 40,
        reviewer_receipts=(_reviewer(),),
        disagreement=True,
    )
    deterministic = route_adversarial_review(
        ClaimReconciliation(False, ImpactLevel.RED, (), True),
        InvariantEvaluation(True, ()),
        author_identity="author-a",
        head_sha="a" * 40,
        reviewer_receipts=(_reviewer(), _reviewer("reviewer-c")),
    )

    assert disagreement.state == NEEDS_OPERATOR
    assert deterministic.state == REJECTED
