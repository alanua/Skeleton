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


def test_disable_schedule_preserves_existing_occurrence_history(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    schedule, _ = store.register(_spec(), now=50)
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

    disabled = store.set_enabled(schedule.spec.schedule_id, False)

    assert disabled.enabled is False
    assert store.list_enabled() == ()
    history = store.list_occurrences(schedule.spec.schedule_id)
    assert len(history) == 1
    assert history[0].occurrence_id == occurrence_id
    assert history[0].state == "done"


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


def test_stale_or_foreign_run_lease_renewal_is_rejected(tmp_path) -> None:
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
    claimed = store.claim_next_pending(now=100, owner="worker-a", lease_seconds=5)

    assert claimed is not None
    assert (
        store.renew_running_claim(
            occurrence_id, owner="worker-b", lease_seconds=5, now=101
        )
        is False
    )
    assert (
        store.renew_running_claim(
            occurrence_id, owner="worker-a", lease_seconds=5, now=105
        )
        is True
    )
    assert (
        store.renew_running_claim(
            occurrence_id, owner="worker-a", lease_seconds=5, now=111
        )
        is False
    )
    assert store.recover_stale_running(
        now=111, stale_after_seconds=5, max_attempts=2
    ) == {"retried": 1, "needs_operator": 0}
    assert store.get_occurrence(occurrence_id).state == "pending"  # type: ignore[union-attr]


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
