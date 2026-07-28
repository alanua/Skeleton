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


def test_recover_stale_running_requires_operator(tmp_path) -> None:
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
    assert store.recover_stale_running(now=1000, stale_after_seconds=100) == 1
    occurrence = store.list_occurrences(schedule.spec.schedule_id)[0]
    assert occurrence.state == "needs_operator"
    assert occurrence.reason == "STALE_RUNNING_RECOVERY"


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
