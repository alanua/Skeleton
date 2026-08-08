from __future__ import annotations

from core.review_gate import (
    durable_needs_operator_result,
    continue_from_internal_review_verdict,
    ensure_draft_pr_review_continuation,
)
from core.scheduler_store import SchedulerStore


HEAD = "a" * 40


def test_draft_pr_enqueues_one_internal_review_after_restart(tmp_path) -> None:
    db_path = tmp_path / "scheduler.sqlite3"
    created = ensure_draft_pr_review_continuation(
        SchedulerStore(db_path),
        repository="alanua/Skeleton",
        pr_number=2294,
        head_sha=HEAD,
        source_issue=2295,
        allowed_files=["core/review_gate.py"],
        now=100,
    )
    replay = ensure_draft_pr_review_continuation(
        SchedulerStore(db_path),
        repository="alanua/Skeleton",
        pr_number=2294,
        head_sha=HEAD,
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
        expected_head_sha=HEAD,
        observed_head_sha=HEAD,
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
        expected_head_sha=HEAD,
        observed_head_sha=HEAD,
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
        expected_head_sha=HEAD,
        observed_head_sha=HEAD,
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
        expected_head_sha=HEAD,
        observed_head_sha=HEAD,
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
        expected_head_sha=HEAD,
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
        head_sha=HEAD,
        permitted_merge_method="squash",
        policy_reason=str(continuation.payload["policy_reason"]),
        next_step=str(continuation.payload["next_step"]),
    )

    assert continuation.payload["review_verdict"] == "NEEDS_OPERATOR"
    assert continuation.payload["merge_executed"] is False
    assert result == {
        "schema": "skeleton.runner_control_result.v1",
        "status": "NEEDS_OPERATOR",
        "repository": "alanua/Skeleton",
        "pr_number": 2294,
        "head_sha": HEAD,
        "permitted_merge_method": "squash",
        "policy_reason": "stale_or_moved_head",
        "next_step": "operator_review_required",
        "public_safe": True,
        "external_side_effects_executed": False,
    }
