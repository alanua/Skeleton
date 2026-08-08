from __future__ import annotations

from core.scheduler_models import ScheduleSpec
from core.scheduler_store import SchedulerStore
from core.shared_dispatch import PRIVACY_PUBLIC_SAFE
from core.review_gate import ensure_draft_pr_review_continuation
from core.notification_policy import operator_notification_ledger_key
from scripts.scheduler_runtime import run_scheduler_tick


HEAD_SHA = "a" * 40


def _review_state(*, head_sha: str = HEAD_SHA, files=None, checks=None):
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
                    "approved_capabilities": ["loop:state_write"],
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
        review_state_reader=lambda payload: _review_state(),
    )

    store = SchedulerStore(scheduler_db)
    receipts = [
        dispatch
        for occurrence in store.list_occurrences(
            store.list_enabled()[0].spec.schedule_id
        )
        for dispatch in store.list_dispatch_receipts(occurrence.occurrence_id)
    ]
    assert receipt["dispatch"]["done"] >= 1
    assert all(item["reason"] != "ROUTE_NOT_ALLOWLISTED" for item in receipts)
    assert any(
        item["result"]["route_receipt"]["internal_review_verdict"] == "APPROVE"
        for item in receipts
    )


def test_production_path_request_changes_creates_one_repair_and_rereview(tmp_path) -> None:
    scheduler_db = tmp_path / "scheduler.sqlite3"
    loop_db = tmp_path / "loop.sqlite3"
    ensure_draft_pr_review_continuation(
        SchedulerStore(scheduler_db),
        repository="alanua/Skeleton",
        pr_number=2302,
        head_sha=HEAD_SHA,
        source_issue=2301,
        allowed_files=["docs/AUTONOMOUS_REVIEW_GATE.md"],
        now=100,
    )

    first = run_scheduler_tick(
        scheduler_db_path=str(scheduler_db),
        loop_state_db_path=str(loop_db),
        now=100,
        review_state_reader=lambda payload: {
            **_review_state(),
            "review_findings": ["missing bounded test"],
        },
    )
    second = run_scheduler_tick(
        scheduler_db_path=str(scheduler_db),
        loop_state_db_path=str(loop_db),
        now=100,
        review_state_reader=lambda payload: {
            **_review_state(),
            "review_findings": ["missing bounded test"],
        },
    )
    run_scheduler_tick(
        scheduler_db_path=str(scheduler_db),
        loop_state_db_path=str(loop_db),
        now=101,
        review_state_reader=lambda payload: _review_state(),
    )

    store = SchedulerStore(scheduler_db)
    repair_occurrences = [
        occurrence
        for schedule in store.list_enabled()
        for occurrence in store.list_occurrences(schedule.spec.schedule_id)
        if occurrence.proposal["payload"].get("next_step") == "bounded_repair_existing_pr_branch"
    ]
    rereviews = [
        occurrence
        for schedule in store.list_enabled()
        for occurrence in store.list_occurrences(schedule.spec.schedule_id)
        if occurrence.proposal["payload"].get("repair_parent_reason")
        == "review_findings_present"
    ]
    assert first["dispatch"]["done"] >= 1
    assert second["dispatch"]["done"] == 0
    assert len(repair_occurrences) == 1
    assert len(rereviews) == 1


def test_production_path_protected_pr_materializes_needs_operator_once(tmp_path) -> None:
    scheduler_db = tmp_path / "scheduler.sqlite3"
    loop_db = tmp_path / "loop.sqlite3"
    ensure_draft_pr_review_continuation(
        SchedulerStore(scheduler_db),
        repository="alanua/Skeleton",
        pr_number=2302,
        head_sha=HEAD_SHA,
        source_issue=2301,
        allowed_files=["scripts/runner_poll_github_tasks.py"],
        now=100,
    )

    run_scheduler_tick(
        scheduler_db_path=str(scheduler_db),
        loop_state_db_path=str(loop_db),
        now=100,
        review_state_reader=lambda payload: _review_state(
            files=["scripts/runner_poll_github_tasks.py"]
        ),
    )
    run_scheduler_tick(
        scheduler_db_path=str(scheduler_db),
        loop_state_db_path=str(loop_db),
        now=100,
        review_state_reader=lambda payload: _review_state(
            files=["scripts/runner_poll_github_tasks.py"]
        ),
    )

    store = SchedulerStore(scheduler_db)
    needs_operator = [
        occurrence
        for schedule in store.list_enabled()
        for occurrence in store.list_occurrences(schedule.spec.schedule_id)
        if occurrence.proposal["payload"].get("next_step") == "operator_review_required"
    ]
    notification = store.get_operational_event(
        operator_notification_ledger_key(
            repository="alanua/Skeleton",
            pr_number=2302,
            head_sha=HEAD_SHA,
            reason="operator approval file changed: scripts/runner_poll_github_tasks.py",
        )
    )
    assert len(needs_operator) == 1
    assert needs_operator[0].proposal["payload"]["permitted_merge_method"] == "squash"
    assert notification is not None


def test_production_path_stale_head_fails_closed(tmp_path) -> None:
    scheduler_db = tmp_path / "scheduler.sqlite3"
    loop_db = tmp_path / "loop.sqlite3"
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
        review_state_reader=lambda payload: _review_state(head_sha="b" * 40),
    )

    store = SchedulerStore(scheduler_db)
    do_not_merge = [
        occurrence
        for schedule in store.list_enabled()
        for occurrence in store.list_occurrences(schedule.spec.schedule_id)
        if occurrence.proposal["payload"].get("next_step") == "internal_repair_supersede_dependency"
    ]
    assert len(do_not_merge) == 1
    assert do_not_merge[0].proposal["payload"]["review_verdict"] == "DO_NOT_MERGE"
