from __future__ import annotations

from core.review_gate import (
    APPROVE,
    DO_NOT_MERGE,
    NEEDS_OPERATOR,
    REQUEST_CHANGES,
    ReviewGateRequest,
    evaluate_review_gate,
    render_review_gate_report,
)
from core.shared_dispatch import shared_continuation_receipt


HEAD_SHA = "a" * 40


def _pr(**updates: object) -> dict[str, object]:
    pr: dict[str, object] = {
        "state": "open",
        "draft": True,
        "mergeable": True,
        "mergeable_state": "clean",
        "base": {"ref": "main", "sha": "b" * 40},
        "head": {"ref": "runner/issue-2244", "sha": HEAD_SHA},
    }
    pr.update(updates)
    return pr


def _request(**updates: object) -> ReviewGateRequest:
    values = {
        "repository": "alanua/Skeleton",
        "pr_number": 2244,
        "expected_head_sha": HEAD_SHA,
        "pr": _pr(),
        "changed_files": ("docs/AUTONOMOUS_REVIEW_GATE.md",),
        "validation_state": "success",
        "validation_reason": "none",
    }
    values.update(updates)
    return ReviewGateRequest(**values)


def test_draft_pr_with_safe_diff_and_green_validation_approves_without_operator() -> None:
    decision = evaluate_review_gate(_request())

    assert decision.verdict == APPROVE
    assert decision.notify_operator is False
    assert decision.continuation == "continue_authorized_workflow"
    assert shared_continuation_receipt(decision)["loop_event"] == "STEP_SUCCEEDED"


def test_review_findings_request_changes_and_create_one_bounded_repair_task() -> None:
    decision = evaluate_review_gate(
        _request(review_findings=("missing regression test",))
    )

    assert decision.verdict == REQUEST_CHANGES
    assert decision.notify_operator is False
    assert decision.repair_task is not None
    assert decision.repair_task["uses_existing_pr"] is True
    assert decision.repair_task["public_safe"] is True
    assert shared_continuation_receipt(decision)["loop_event"] == "STEP_FAILED"


def test_review_replay_reuses_existing_repair_task() -> None:
    first = evaluate_review_gate(_request(review_findings=("missing regression test",)))
    report = render_review_gate_report(first)
    second = evaluate_review_gate(
        _request(review_findings=("missing regression test",)),
        prior_receipts=[report],
    )

    assert first.repair_task is not None
    assert second.repair_task is not None
    assert second.repair_task["task_id"] == first.repair_task["task_id"]
    assert second.repair_task["reused_existing"] is True


def test_self_review_api_rejection_does_not_block_typed_internal_verdict() -> None:
    decision = evaluate_review_gate(_request())

    assert decision.verdict == APPROVE
    assert decision.github_review_required is False
    assert "internal_review_verdict=APPROVE" in render_review_gate_report(decision)


def test_protected_change_escalates_exactly_to_needs_operator() -> None:
    decision = evaluate_review_gate(
        _request(changed_files=("scripts/runner_poll_github_tasks.py",))
    )

    assert decision.verdict == NEEDS_OPERATOR
    assert decision.notify_operator is True
    assert shared_continuation_receipt(decision)["loop_event"] == "OPERATOR_REQUIRED"


def test_stale_or_moved_head_fails_closed_before_merge() -> None:
    decision = evaluate_review_gate(_request(expected_head_sha="b" * 40))

    assert decision.verdict == DO_NOT_MERGE
    assert decision.reason == "expected_head_sha_mismatch"
    assert decision.notify_operator is False
    assert shared_continuation_receipt(decision)["loop_event"] == "REVIEW_REQUIRED"
