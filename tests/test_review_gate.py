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
    schedule_verified_repair_done,
    shared_continuation_receipt,
)
from core.scheduler_store import SchedulerStore


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


def test_safe_diff_and_green_validation_approves_without_operator() -> None:
    decision = evaluate_review_gate(_request())

    assert decision.verdict == APPROVE
    assert decision.notify_operator is False
    assert shared_continuation_receipt(decision)["loop_event"] == "STEP_SUCCEEDED"


def test_review_findings_request_changes_and_replay_reuses_repair_task() -> None:
    first = evaluate_review_gate(_request(review_findings=("missing regression test",)))
    second = evaluate_review_gate(
        _request(review_findings=("missing regression test",)),
        prior_receipts=[render_review_gate_report(first)],
    )

    assert first.verdict == REQUEST_CHANGES
    assert first.repair_task is not None
    assert second.repair_task is not None
    assert second.repair_task["task_id"] == first.repair_task["task_id"]
    assert second.repair_task["reused_existing"] is True


def test_protected_change_escalates_to_needs_operator() -> None:
    decision = evaluate_review_gate(
        _request(changed_files=("scripts/runner_poll_github_tasks.py",))
    )

    assert decision.verdict == NEEDS_OPERATOR
    assert decision.notify_operator is True
    assert decision.operator_packet is not None


def test_stale_or_moved_head_fails_closed_before_merge() -> None:
    decision = evaluate_review_gate(_request(expected_head_sha="b" * 40))

    assert decision.verdict == DO_NOT_MERGE
    assert decision.reason == "expected_head_sha_mismatch"
    assert decision.notify_operator is False


def test_draft_pr_enqueues_one_canonical_internal_review_after_restart(tmp_path) -> None:
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
    occurrence = store.list_occurrences(created.schedule_id)[0]
    assert created.created is True
    assert replay.created is False
    assert occurrence.proposal["route_type"] == "runner"
    assert occurrence.proposal["route_id"] == "internal_review_control"
    assert occurrence.proposal["payload"]["requested_capabilities"] == ["repository_read"]
    assert "approved_capabilities" not in occurrence.proposal["payload"]


def test_verdict_materialization_paths_are_durable_and_idempotent(tmp_path) -> None:
    db_path = tmp_path / "scheduler.sqlite3"
    approve = continue_from_internal_review_verdict(
        SchedulerStore(db_path),
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
    repair = continue_from_internal_review_verdict(
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
        now=101,
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
        now=101,
    )

    assert approve.next_step == "operator_review_required"
    assert approve.payload["review_verdict"] == NEEDS_OPERATOR
    assert approve.payload["operator_notification_allowed"] is True
    assert repair.next_step == "bounded_repair_replacement_pr"
    assert replay.created is False
    assert replay.occurrence_id == repair.occurrence_id


def test_protected_or_stale_head_produces_exact_needs_operator_packet(tmp_path) -> None:
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
    assert result["repository"] == "alanua/Skeleton"
    assert result["pr_number"] == 2294
    assert result["head_sha"] == HEAD_SHA
    assert result["policy_reason"] == "stale_or_moved_head"
    assert result["next_step"] == "operator_review_required"


def test_verified_repair_done_callback_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "scheduler.sqlite3"
    first = schedule_verified_repair_done(
        SchedulerStore(db_path),
        repository="alanua/Skeleton",
        replacement_pr_number=2294,
        replacement_head_sha=HEAD_SHA,
        source_issue=2295,
        allowed_files=["core/review_gate.py"],
        repair_task_id="repair-pr-2294-abc",
        repair_idempotency_key="repair-key",
        publication_status="DONE",
        now=100,
        reviewed_pr_number=2293,
        reviewed_head_sha="b" * 40,
    )
    second = schedule_verified_repair_done(
        SchedulerStore(db_path),
        repository="alanua/Skeleton",
        replacement_pr_number=2294,
        replacement_head_sha=HEAD_SHA,
        source_issue=2295,
        allowed_files=["core/review_gate.py"],
        repair_task_id="repair-pr-2294-abc",
        repair_idempotency_key="repair-key",
        publication_status="DONE",
        now=100,
        reviewed_pr_number=2293,
        reviewed_head_sha="b" * 40,
    )

    assert first.next_step == "repair_done"
    assert second.created is False
