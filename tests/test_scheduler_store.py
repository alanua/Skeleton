import pytest

from core.scheduler_models import ScheduleSpec, build_execution_proposal, stable_occurrence_id
from core.scheduler_store import SchedulerStore, SchedulerStoreError


def _spec(schedule_id="test.once", once_at=100, route_id="notify.test"):
    return ScheduleSpec.from_mapping(
        {
            "schema": "skeleton.schedule.v1",
            "schedule_id": schedule_id,
            "trigger_kind": "once",
            "cron_expression": None,
            "once_at": once_at,
            "timezone": "UTC",
            "route_type": "notify",
            "route_id": route_id,
            "approval_policy": "notify_only",
            "overlap_policy": "skip",
            "misfire_policy": "run_once",
            "payload": {"private": "value"},
        }
    )


def test_register_is_idempotent_and_versions_changes(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    first, created = store.register(_spec(), now=50)
    replay, replay_created = store.register(_spec(), now=60)
    changed, changed_created = store.register(_spec(route_id="notify.changed"), now=70)
    assert (first.version, created) == (1, True)
    assert (replay.version, replay_created) == (1, False)
    assert (changed.version, changed_created) == (2, True)


def test_occurrence_unique_and_payload_not_in_public_receipt(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    schedule, _ = store.register(_spec(), now=50)
    occurrence_id = stable_occurrence_id(schedule.spec.schedule_id, schedule.version, 100)
    proposal = build_execution_proposal(schedule, occurrence_id=occurrence_id, scheduled_for=100)
    first, created = store.create_occurrence(
        occurrence_id=occurrence_id,
        schedule=schedule,
        scheduled_for=100,
        state="done",
        reason="NOTIFY_ONLY_PROPOSAL",
        proposal=proposal,
        now=100,
    )
    replay, replay_created = store.create_occurrence(
        occurrence_id=occurrence_id,
        schedule=schedule,
        scheduled_for=100,
        state="done",
        reason="NOTIFY_ONLY_PROPOSAL",
        proposal=proposal,
        now=101,
    )
    assert created is True
    assert replay_created is False
    assert replay.occurrence_id == first.occurrence_id
    assert "payload" not in first.public_receipt()


def test_recover_stale_running_retries_before_operator(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    schedule, _ = store.register(_spec(), now=1)
    occurrence_id = stable_occurrence_id(schedule.spec.schedule_id, schedule.version, 100)
    proposal = build_execution_proposal(schedule, occurrence_id=occurrence_id, scheduled_for=100)
    store.create_occurrence(
        occurrence_id=occurrence_id,
        schedule=schedule,
        scheduled_for=100,
        state="pending",
        reason="DISPATCH_REQUIRED",
        proposal=proposal,
        now=100,
    )
    store.transition_occurrence(
        occurrence_id,
        expected_states={"pending"},
        new_state="running",
        reason="DISPATCH_STARTED",
        now=101,
    )
    assert store.recover_stale_running(now=1000, stale_after_seconds=100) == {
        "retried": 1,
        "needs_operator": 0,
    }
    occurrence = store.list_occurrences(schedule.spec.schedule_id)[0]
    assert occurrence.state == "pending"
    assert occurrence.reason == "STALE_RUNNING_RETRY"


def test_atomic_claim_sets_attempt_and_prevents_duplicate_worker(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    schedule, _ = store.register(_spec(), now=1)
    occurrence_id = stable_occurrence_id(schedule.spec.schedule_id, schedule.version, 100)
    proposal = build_execution_proposal(schedule, occurrence_id=occurrence_id, scheduled_for=100)
    store.create_occurrence(
        occurrence_id=occurrence_id,
        schedule=schedule,
        scheduled_for=100,
        state="pending",
        reason="DISPATCH_REQUIRED",
        proposal=proposal,
        now=100,
    )

    claimed = store.claim_next_pending(now=101)
    duplicate = store.claim_next_pending(now=101)

    assert claimed is not None
    assert claimed.state == "running"
    assert claimed.attempt == 1
    assert claimed.idempotency_key == f"{occurrence_id}:attempt:1"
    assert duplicate is None


def test_transition_conflict_fails_closed(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    schedule, _ = store.register(_spec(), now=1)
    occurrence_id = stable_occurrence_id(schedule.spec.schedule_id, schedule.version, 100)
    proposal = build_execution_proposal(schedule, occurrence_id=occurrence_id, scheduled_for=100)
    store.create_occurrence(
        occurrence_id=occurrence_id,
        schedule=schedule,
        scheduled_for=100,
        state="done",
        reason="NOTIFY_ONLY_PROPOSAL",
        proposal=proposal,
        now=100,
    )
    with pytest.raises(SchedulerStoreError):
        store.transition_occurrence(
            occurrence_id,
            expected_states={"pending"},
            new_state="running",
            reason="DISPATCH_STARTED",
            now=101,
        )


def test_dispatch_receipt_exact_duplicate_is_no_op(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    schedule, _ = store.register(_spec(), now=1)
    occurrence_id = stable_occurrence_id(schedule.spec.schedule_id, schedule.version, 100)
    proposal = build_execution_proposal(schedule, occurrence_id=occurrence_id, scheduled_for=100)
    store.create_occurrence(
        occurrence_id=occurrence_id,
        schedule=schedule,
        scheduled_for=100,
        state="pending",
        reason="DISPATCH_REQUIRED",
        proposal=proposal,
        now=100,
    )
    claimed = store.claim_next_pending(now=101)
    assert claimed is not None

    kwargs = {
        "occurrence_id": occurrence_id,
        "attempt": claimed.attempt,
        "idempotency_key": claimed.idempotency_key or "",
        "status": "done",
        "reason": "SYNTHETIC_DONE",
        "evidence_ref": "dispatch:synthetic-done",
        "result": {
            "schema": "skeleton.scheduler_dispatch_receipt.v1",
            "occurrence_id": occurrence_id,
            "attempt": claimed.attempt,
            "idempotency_key": claimed.idempotency_key,
            "public_safe": True,
            "external_side_effects_executed": False,
        },
        "parent_receipt_id": None,
    }
    first = store.record_dispatch_receipt(**kwargs, now=102)
    replay = store.record_dispatch_receipt(**kwargs, now=103)

    receipts = store.list_dispatch_receipts(occurrence_id)
    assert replay == first
    assert len(receipts) == 1
    assert receipts[0]["created_at"] == 102


def test_dispatch_receipt_reused_key_with_different_contract_fails_closed(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    schedule, _ = store.register(_spec(), now=1)
    occurrence_id = stable_occurrence_id(schedule.spec.schedule_id, schedule.version, 100)
    proposal = build_execution_proposal(schedule, occurrence_id=occurrence_id, scheduled_for=100)
    other_occurrence_id = stable_occurrence_id(schedule.spec.schedule_id, schedule.version, 101)
    other_proposal = build_execution_proposal(
        schedule, occurrence_id=other_occurrence_id, scheduled_for=101
    )
    store.create_occurrence(
        occurrence_id=occurrence_id,
        schedule=schedule,
        scheduled_for=100,
        state="pending",
        reason="DISPATCH_REQUIRED",
        proposal=proposal,
        now=100,
    )
    store.create_occurrence(
        occurrence_id=other_occurrence_id,
        schedule=schedule,
        scheduled_for=101,
        state="pending",
        reason="DISPATCH_REQUIRED",
        proposal=other_proposal,
        now=100,
    )
    claimed = store.claim_next_pending(now=101)
    assert claimed is not None
    idempotency_key = claimed.idempotency_key or ""
    store.record_dispatch_receipt(
        occurrence_id=occurrence_id,
        attempt=claimed.attempt,
        idempotency_key=idempotency_key,
        status="done",
        reason="SYNTHETIC_DONE",
        evidence_ref="dispatch:synthetic-done",
        result={
            "schema": "skeleton.scheduler_dispatch_receipt.v1",
            "occurrence_id": occurrence_id,
            "attempt": claimed.attempt,
            "idempotency_key": idempotency_key,
            "public_safe": True,
            "external_side_effects_executed": False,
        },
        now=102,
    )

    with pytest.raises(SchedulerStoreError) as exc:
        store.record_dispatch_receipt(
            occurrence_id=other_occurrence_id,
            attempt=claimed.attempt + 1,
            idempotency_key=idempotency_key,
            status="failed",
            reason="SYNTHETIC_FAILED",
            evidence_ref="dispatch:synthetic-failed",
            result={
                "schema": "skeleton.scheduler_dispatch_receipt.v1",
                "occurrence_id": other_occurrence_id,
                "attempt": claimed.attempt + 1,
                "idempotency_key": idempotency_key,
                "public_safe": True,
                "external_side_effects_executed": False,
            },
            now=103,
        )

    assert str(exc.value) == "DISPATCH_RECEIPT_IDEMPOTENCY_CONFLICT"
    receipts = store.list_dispatch_receipts(occurrence_id)
    assert len(receipts) == 1
    assert receipts[0]["attempt"] == claimed.attempt
    assert receipts[0]["status"] == "done"
