from __future__ import annotations

from core.scheduler_models import ScheduleSpec
from core.scheduler_store import SchedulerStore
from core.shared_dispatch import PRIVACY_PUBLIC_SAFE
from core.review_gate import (
    ReviewControlAdapters,
    ensure_draft_pr_review_continuation,
    schedule_verified_repair_done,
)
from core.notification_policy import operator_notification_ledger_key
from scripts.scheduler_runtime import run_scheduler_tick


HEAD_SHA = "a" * 40


def _review_state(*, head_sha: str = HEAD_SHA, files=None, checks=None, findings=None):
    return {
        "pr": {
            "state": "open",
            "draft": True,
            "mergeable": True,
            "mergeable_state": "clean",
            "base": {"ref": "main", "sha": "b" * 40},
            "head": {"ref": "runner/issue-2302", "sha": head_sha},
        },
        "files": [{"filename": path} for path in (files or ["docs/AUTONOMOUS_REVIEW_GATE.md"])],
        "compare": {"status": "ahead", "ahead_by": 1, "behind_by": 0},
        "combined_status": {"state": "success", "statuses": [{"state": "success"}]},
        "check_runs": checks or [{"status": "completed", "conclusion": "success"}],
        "review_findings": findings or [],
    }


def test_scheduler_runtime_runs_synthetic_loop_canary(tmp_path) -> None:
    scheduler_db = tmp_path / "scheduler.sqlite3"
    loop_db = tmp_path / "loop.sqlite3"
    store = SchedulerStore(scheduler_db)
    store.initialize()
    store.register(
        ScheduleSpec.from_mapping(
            {
                "schema": "skeleton.schedule.v1",
                "schedule_id": "runtime.canary",
                "trigger_kind": "once",
                "cron_expression": None,
                "once_at": 100,
                "timezone": "UTC",
                "route_type": "loop",
                "route_id": "loop_engine_packet",
                "approval_policy": "auto_run_low_risk",
                "overlap_policy": "queue_one",
                "misfire_policy": "run_once",
                "payload": {
                    "privacy_boundary": PRIVACY_PUBLIC_SAFE,
                    "bounded": True,
                    "requested_capabilities": ["loop:state_write"],
                    "task_packet": {
                        "schema": "skeleton.loop_runner_packet.v1",
                        "action": "create",
                        "task_id": "runtime-task",
                        "run_id": "runtime-run",
                        "recorded_at": 100,
                        "public_safe": True,
                        "no_secrets": True,
                        "no_runtime_mutation": True,
                        "authority_boundary": {
                            "operational_state_write": True,
                            "external_side_effects_allowed": False,
                            "runtime_mutation_allowed": False,
                        },
                    },
                },
            }
        ),
        now=50,
    )

    receipt = run_scheduler_tick(
        scheduler_db_path=str(scheduler_db),
        loop_state_db_path=str(loop_db),
        now=100,
    )

    assert receipt["dispatch"]["done"] == 1
    assert store.list_occurrences("runtime.canary")[0].state == "done"


def test_run_scheduler_tick_consumes_internal_review_without_route_block(tmp_path) -> None:
    scheduler_db = tmp_path / "scheduler.sqlite3"
    loop_db = tmp_path / "loop.sqlite3"
    approvals = []
    ensure_draft_pr_review_continuation(
        SchedulerStore(scheduler_db),
        repository="alanua/Skeleton",
        pr_number=2302,
        head_sha=HEAD_SHA,
        source_issue=2301,
        allowed_files=["docs/AUTONOMOUS_REVIEW_GATE.md"],
        now=100,
    )

    receipt = run_scheduler_tick(
        scheduler_db_path=str(scheduler_db),
        loop_state_db_path=str(loop_db),
        now=100,
        review_adapters=ReviewControlAdapters(
            state_reader=lambda payload: _review_state(),
            authorized_continuation=lambda packet: approvals.append(packet)
            or {"status": "DONE", "adapter": "authorized"},
        ),
    )

    store = SchedulerStore(scheduler_db)
    receipts = [
        dispatch
        for schedule in store.list_enabled()
        for occurrence in store.list_occurrences(schedule.spec.schedule_id)
        for dispatch in store.list_dispatch_receipts(occurrence.occurrence_id)
    ]
    assert receipt["dispatch"]["done"] >= 1
    assert len(approvals) == 1
    assert all(item["reason"] != "ROUTE_NOT_ALLOWLISTED" for item in receipts)
    assert any(
        item["result"]["route_receipt"].get("internal_review_verdict") == "APPROVE"
        for item in receipts
    )


def test_production_path_request_changes_enqueues_one_repair_and_waits_for_done(tmp_path) -> None:
    scheduler_db = tmp_path / "scheduler.sqlite3"
    loop_db = tmp_path / "loop.sqlite3"
    repairs = []
    ensure_draft_pr_review_continuation(
        SchedulerStore(scheduler_db),
        repository="alanua/Skeleton",
        pr_number=2302,
        head_sha=HEAD_SHA,
        source_issue=2301,
        allowed_files=["docs/AUTONOMOUS_REVIEW_GATE.md"],
        now=100,
    )

    adapters = ReviewControlAdapters(
        state_reader=lambda payload: _review_state(findings=["missing bounded test"]),
        repair_enqueue=lambda task: repairs.append(task) or {"status": "DONE", "task": task["task_id"]},
    )
    first = run_scheduler_tick(
        scheduler_db_path=str(scheduler_db),
        loop_state_db_path=str(loop_db),
        now=100,
        review_adapters=adapters,
    )
    second = run_scheduler_tick(
        scheduler_db_path=str(scheduler_db),
        loop_state_db_path=str(loop_db),
        now=100,
        review_adapters=adapters,
    )

    store = SchedulerStore(scheduler_db)
    rereviews_before_done = [
        occurrence
        for schedule in store.list_enabled()
        for occurrence in store.list_occurrences(schedule.spec.schedule_id)
        if occurrence.proposal["payload"].get("repair_parent_reason")
    ]
    assert first["dispatch"]["done"] >= 1
    assert second["dispatch"]["done"] == 0
    assert len(repairs) == 1
    assert rereviews_before_done == []

    schedule_verified_repair_done(
        SchedulerStore(scheduler_db),
        repository="alanua/Skeleton",
        pr_number=2302,
        head_sha=HEAD_SHA,
        source_issue=2301,
        allowed_files=["docs/AUTONOMOUS_REVIEW_GATE.md"],
        repair_task_id=str(repairs[0]["task_id"]),
        repair_idempotency_key=str(repairs[0]["idempotency_key"]),
        publication_status="DONE",
        now=101,
    )
    run_scheduler_tick(
        scheduler_db_path=str(scheduler_db),
        loop_state_db_path=str(loop_db),
        now=101,
        review_adapters=ReviewControlAdapters(
            state_reader=lambda payload: _review_state(),
            authorized_continuation=lambda packet: {"status": "DONE"},
        ),
    )
    rereviews_after_done = [
        occurrence
        for schedule in store.list_enabled()
        for occurrence in store.list_occurrences(schedule.spec.schedule_id)
        if occurrence.proposal["payload"].get("repair_parent_reason")
    ]
    assert len(rereviews_after_done) == 1


def test_production_path_protected_pr_delivers_needs_operator_once(tmp_path) -> None:
    scheduler_db = tmp_path / "scheduler.sqlite3"
    loop_db = tmp_path / "loop.sqlite3"
    merges = []
    deliveries = []
    ensure_draft_pr_review_continuation(
        SchedulerStore(scheduler_db),
        repository="alanua/Skeleton",
        pr_number=2302,
        head_sha=HEAD_SHA,
        source_issue=2301,
        allowed_files=["scripts/runner_poll_github_tasks.py"],
        now=100,
    )
    adapters = ReviewControlAdapters(
        state_reader=lambda payload: _review_state(
            files=["scripts/runner_poll_github_tasks.py"]
        ),
        authorized_continuation=lambda packet: merges.append(packet) or {"status": "DONE"},
        needs_operator_delivery=lambda packet: deliveries.append(packet) or {"status": "DONE"},
    )

    run_scheduler_tick(
        scheduler_db_path=str(scheduler_db),
        loop_state_db_path=str(loop_db),
        now=100,
        review_adapters=adapters,
    )
    run_scheduler_tick(
        scheduler_db_path=str(scheduler_db),
        loop_state_db_path=str(loop_db),
        now=100,
        review_adapters=adapters,
    )

    store = SchedulerStore(scheduler_db)
    notification = store.get_operational_event(
        operator_notification_ledger_key(
            repository="alanua/Skeleton",
            pr_number=2302,
            head_sha=HEAD_SHA,
            reason="operator approval file changed: scripts/runner_poll_github_tasks.py",
        )
    )
    assert merges == []
    assert len(deliveries) == 1
    assert deliveries[0]["repository"] == "alanua/Skeleton"
    assert deliveries[0]["pr_number"] == 2302
    assert deliveries[0]["head_sha"] == HEAD_SHA
    assert deliveries[0]["permitted_merge_method"] == "squash"
    assert notification is not None


def test_production_path_stale_head_fails_closed_without_actions(tmp_path) -> None:
    scheduler_db = tmp_path / "scheduler.sqlite3"
    loop_db = tmp_path / "loop.sqlite3"
    repairs = []
    merges = []
    ensure_draft_pr_review_continuation(
        SchedulerStore(scheduler_db),
        repository="alanua/Skeleton",
        pr_number=2302,
        head_sha=HEAD_SHA,
        source_issue=2301,
        allowed_files=["docs/AUTONOMOUS_REVIEW_GATE.md"],
        now=100,
    )

    run_scheduler_tick(
        scheduler_db_path=str(scheduler_db),
        loop_state_db_path=str(loop_db),
        now=100,
        review_adapters=ReviewControlAdapters(
            state_reader=lambda payload: _review_state(head_sha="b" * 40),
            repair_enqueue=lambda packet: repairs.append(packet) or {"status": "DONE"},
            authorized_continuation=lambda packet: merges.append(packet) or {"status": "DONE"},
        ),
    )

    store = SchedulerStore(scheduler_db)
    do_not_merge = [
        occurrence
        for schedule in store.list_enabled()
        for occurrence in store.list_occurrences(schedule.spec.schedule_id)
        if occurrence.proposal["payload"].get("next_step") == "internal_repair_supersede_dependency"
    ]
    assert repairs == []
    assert merges == []
    assert len(do_not_merge) == 1
    assert do_not_merge[0].proposal["payload"]["review_verdict"] == "DO_NOT_MERGE"


def test_unknown_internal_control_action_fails_closed(tmp_path) -> None:
    scheduler_db = tmp_path / "scheduler.sqlite3"
    loop_db = tmp_path / "loop.sqlite3"
    store = SchedulerStore(scheduler_db)
    ensure_draft_pr_review_continuation(
        store,
        repository="alanua/Skeleton",
        pr_number=2302,
        head_sha=HEAD_SHA,
        source_issue=2301,
        allowed_files=["docs/AUTONOMOUS_REVIEW_GATE.md"],
        now=100,
    )
    schedule = store.list_enabled()[0]
    payload = dict(schedule.spec.payload)
    payload["next_step"] = "unknown"
    from core.review_gate import enqueue_internal_review_control

    enqueue_internal_review_control(store, payload=payload, now=101)

    receipt = run_scheduler_tick(
        scheduler_db_path=str(scheduler_db),
        loop_state_db_path=str(loop_db),
        now=101,
        review_adapters=ReviewControlAdapters(state_reader=lambda payload: _review_state()),
    )
    receipts = [
        dispatch
        for schedule in store.list_enabled()
        for occurrence in store.list_occurrences(schedule.spec.schedule_id)
        for dispatch in store.list_dispatch_receipts(occurrence.occurrence_id)
    ]

    assert receipt["dispatch"]["needs_operator"] >= 1
    assert any(item["reason"] == "UNKNOWN_INTERNAL_REVIEW_STEP" for item in receipts)
