from __future__ import annotations

from core.loop_controller import LoopEvent
from core.review_gate import APPROVE, DO_NOT_MERGE, NEEDS_OPERATOR, REQUEST_CHANGES, ReviewGateDecision


def loop_event_for_review_gate(decision: ReviewGateDecision) -> LoopEvent:
    if decision.verdict == APPROVE:
        return LoopEvent.STEP_SUCCEEDED
    if decision.verdict == REQUEST_CHANGES:
        return LoopEvent.STEP_FAILED
    if decision.verdict == NEEDS_OPERATOR:
        return LoopEvent.OPERATOR_REQUIRED
    if decision.verdict == DO_NOT_MERGE:
        return LoopEvent.REVIEW_REQUIRED
    raise ValueError("unsupported internal review verdict")


def shared_continuation_receipt(decision: ReviewGateDecision) -> dict[str, object]:
    event = loop_event_for_review_gate(decision)
    return {
        "schema": "skeleton.shared_dispatch.review_continuation.v1",
        "internal_review_verdict": decision.verdict,
        "loop_event": event.value,
        "continuation": decision.continuation,
        "notify_operator": decision.notify_operator,
        "receipt_id": decision.receipt_id,
    }
