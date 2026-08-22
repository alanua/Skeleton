from __future__ import annotations

from dataclasses import dataclass

from core.capability_model import ImpactLevel


PRODUCTION_READY = "PRODUCTION_READY"
NEEDS_OPERATOR = "NEEDS_OPERATOR"
NEEDS_REVIEW = "NEEDS_REVIEW"
REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class ProductionQaReceipt:
    state: str
    exact_head_sha: str
    changed_files: tuple[str, ...]
    impact_level: ImpactLevel
    reasons: tuple[str, ...]
    next_action: str


def production_qa_receipt(
    *,
    exact_head_sha: str,
    changed_files: tuple[str, ...],
    impact_level: ImpactLevel,
    reasons: tuple[str, ...],
    state: str,
) -> ProductionQaReceipt:
    if state == "RUNTIME_PROVEN":
        raise ValueError("RUNTIME_PROVEN_NOT_MATERIALIZED_IN_PHASE4")
    if state not in {PRODUCTION_READY, NEEDS_OPERATOR, NEEDS_REVIEW, REJECTED}:
        raise ValueError("INVALID_PRODUCTION_QA_STATE")
    return ProductionQaReceipt(
        state=state,
        exact_head_sha=exact_head_sha.lower(),
        changed_files=tuple(sorted(changed_files)),
        impact_level=impact_level,
        reasons=tuple(sorted(set(reasons))),
        next_action="EXACT_HEAD_OPERATOR_REVIEW_THEN_PHASE5_RUNTIME_PROOF",
    )
