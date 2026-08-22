from __future__ import annotations

from dataclasses import dataclass

from core.architecture_invariants import InvariantEvaluation
from core.capability_model import ImpactLevel
from core.claim_reconciliation import ClaimReconciliation
from core.evidence_authenticity import ReviewerReceipt, authentic_review_receipt
from core.quality_evidence import NEEDS_OPERATOR, NEEDS_REVIEW, PRODUCTION_READY, REJECTED


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    state: str
    required_reviewer_receipts: int
    operator_required: bool
    reasons: tuple[str, ...]


def route_adversarial_review(
    reconciliation: ClaimReconciliation,
    invariants: InvariantEvaluation,
    *,
    author_identity: str,
    head_sha: str,
    reviewer_receipts: tuple[ReviewerReceipt, ...] = (),
    configured_quorum: int = 2,
    disagreement: bool = False,
) -> ReviewDecision:
    reasons = set(reconciliation.reasons) | set(invariants.failures)
    if not invariants.passed or reconciliation.deterministic_failure:
        reasons.add("deterministic_failure")
        return ReviewDecision(REJECTED, 0, False, tuple(sorted(reasons)))
    if disagreement:
        reasons.add("review_disagreement")
        return ReviewDecision(NEEDS_OPERATOR, configured_quorum, True, tuple(sorted(reasons)))

    authentic = tuple(
        receipt
        for receipt in reviewer_receipts
        if authentic_review_receipt(
            author_identity=author_identity,
            reviewer=receipt,
            current_head_sha=head_sha,
        )
    )
    if reconciliation.level is ImpactLevel.GREEN:
        return ReviewDecision(PRODUCTION_READY, 0, False, tuple(sorted(reasons)))
    if reconciliation.level is ImpactLevel.YELLOW:
        if len(authentic) >= 1:
            return ReviewDecision(PRODUCTION_READY, 1, False, tuple(sorted(reasons)))
        reasons.add("independent_review_required")
        return ReviewDecision(NEEDS_REVIEW, 1, False, tuple(sorted(reasons)))
    required = max(1, configured_quorum)
    if len(authentic) >= required:
        reasons.add("operator_gate_required")
        return ReviewDecision(NEEDS_OPERATOR, required, True, tuple(sorted(reasons)))
    reasons.add("quorum_review_required")
    return ReviewDecision(NEEDS_REVIEW, required, reconciliation.level is ImpactLevel.PROTECTED, tuple(sorted(reasons)))
