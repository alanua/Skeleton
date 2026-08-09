from __future__ import annotations

from core.review_gate import (
    APPROVE,
    DO_NOT_MERGE,
    NEEDS_OPERATOR,
    REQUEST_CHANGES,
    ReviewGateRequest,
    continue_from_internal_review_verdict,
    durable_needs_operator_result,
    ensure_draft_pr_review_continuation,
    evaluate_review_gate,
    render_review_gate_report,
)
from core.scheduler_store import SchedulerStore
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


def test_draft_pr_enqueues_one_internal_review_after_restart(tmp_path) -> None:
    db_path = tmp_path / "scheduler.sqlite3"
    created = ensure_draft_pr_review_continuation(
        SchedulerStore(db_path),
        repository="alanua/Skeleton",
        pr_number=2294,
        head_sha=HEAD_SHA,
        source_issue=2295,
        allowed_files=["core/review_gate.py"],
        now=100,
    )
    replay = ensure_draft_pr_review_continuation(
        SchedulerStore(db_path),
        repository="alanua/Skeleton",
        pr_number=2294,
        head_sha=HEAD_SHA,
        source_issue=2295,
        allowed_files=["core/review_gate.py"],
        now=100,
    )

    store = SchedulerStore(db_path)
    assert created.created is True
    assert replay.created is False
    assert replay.occurrence_id == created.occurrence_id
    assert len(store.list_occurrences(created.schedule_id)) == 1
    assert created.payload["requires_chat_plus"] is False
    assert "current_diff" in created.payload["review_scope"]


def test_approve_creates_authorized_continuation_without_operator_notification(tmp_path) -> None:
    continuation = continue_from_internal_review_verdict(
        SchedulerStore(tmp_path / "scheduler.sqlite3"),
        verdict="APPROVE",
        repository="alanua/Skeleton",
        pr_number=2294,
        expected_head_sha=HEAD_SHA,
        observed_head_sha=HEAD_SHA,
        permitted_merge_method="squash",
        policy_reason="safe_internal_approve",
        source_issue=2295,
        allowed_files=["core/review_gate.py"],
        now=100,
    )

    assert continuation.next_step == "authorized_merge_continuation"
    assert continuation.payload["operator_notification_allowed"] is False
    assert continuation.payload["merge_executed"] is False


def test_request_changes_reuses_one_bounded_repair_for_existing_pr_branch(tmp_path) -> None:
    db_path = tmp_path / "scheduler.sqlite3"
    first = continue_from_internal_review_verdict(
        SchedulerStore(db_path),
        verdict="REQUEST_CHANGES",
        repository="alanua/Skeleton",
        pr_number=2294,
        expected_head_sha=HEAD_SHA,
        observed_head_sha=HEAD_SHA,
        permitted_merge_method="squash",
        policy_reason="tests_failed",
        source_issue=2295,
        allowed_files=["core/review_gate.py"],
        now=100,
    )
    replay = continue_from_internal_review_verdict(
        SchedulerStore(db_path),
        verdict="REQUEST_CHANGES",
        repository="alanua/Skeleton",
        pr_number=2294,
        expected_head_sha=HEAD_SHA,
        observed_head_sha=HEAD_SHA,
        permitted_merge_method="squash",
        policy_reason="tests_failed",
        source_issue=2295,
        allowed_files=["core/review_gate.py"],
        now=100,
    )

    assert first.next_step == "bounded_repair_existing_pr_branch"
    assert first.payload["bounded"] is True
    assert replay.created is False
    assert replay.occurrence_id == first.occurrence_id


def test_do_not_merge_remains_internal_and_does_not_notify(tmp_path) -> None:
    continuation = continue_from_internal_review_verdict(
        SchedulerStore(tmp_path / "scheduler.sqlite3"),
        verdict="DO_NOT_MERGE",
        repository="alanua/Skeleton",
        pr_number=2294,
        expected_head_sha=HEAD_SHA,
        observed_head_sha=HEAD_SHA,
        permitted_merge_method="squash",
        policy_reason="dependency_missing",
        source_issue=2295,
        allowed_files=["core/review_gate.py"],
        now=100,
    )

    assert continuation.next_step == "internal_repair_supersede_dependency"
    assert continuation.payload["operator_notification_allowed"] is False


def test_protected_or_stale_head_produces_durable_needs_operator_fields(tmp_path) -> None:
    continuation = continue_from_internal_review_verdict(
        SchedulerStore(tmp_path / "scheduler.sqlite3"),
        verdict="APPROVE",
        repository="alanua/Skeleton",
        pr_number=2294,
        expected_head_sha=HEAD_SHA,
        observed_head_sha="b" * 40,
        permitted_merge_method="squash",
        policy_reason="safe_internal_approve",
        source_issue=2295,
        allowed_files=["core/review_gate.py"],
        now=100,
        protected=True,
    )
    result = durable_needs_operator_result(
        repository="alanua/Skeleton",
        pr_number=2294,
        head_sha=HEAD_SHA,
        permitted_merge_method="squash",
        policy_reason=str(continuation.payload["policy_reason"]),
        next_step=str(continuation.payload["next_step"]),
    )

    assert continuation.payload["review_verdict"] == "NEEDS_OPERATOR"
    assert continuation.payload["merge_executed"] is False
    assert result["repository"] == "alanua/Skeleton"
    assert result["pr_number"] == 2294
    assert result["head_sha"] == HEAD_SHA
    assert result["policy_reason"] == "stale_or_moved_head"
    assert result["next_step"] == "operator_review_required"
