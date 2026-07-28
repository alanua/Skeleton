from core.scheduler_engine import SchedulerEngine, SchedulerEngineConfig
from core.scheduler_models import ScheduleSpec, build_execution_proposal, stable_occurrence_id
from core.scheduler_store import SchedulerStore


def _once(
    schedule_id: str,
    once_at: int,
    *,
    approval="notify_only",
    overlap="skip",
    misfire="run_once",
):
    return ScheduleSpec.from_mapping(
        {
            "schema": "skeleton.schedule.v1",
            "schedule_id": schedule_id,
            "trigger_kind": "once",
            "cron_expression": None,
            "once_at": once_at,
            "timezone": "UTC",
            "route_type": "notify",
            "route_id": "notify.test",
            "approval_policy": approval,
            "overlap_policy": overlap,
            "misfire_policy": misfire,
            "payload": {"private": "payload"},
        }
    )


def test_notify_only_tick_creates_once_and_prevents_duplicate(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    store.register(_once("test.once", 100), now=50)
    engine = SchedulerEngine(store)
    first = engine.tick(now=100)
    second = engine.tick(now=100)
    assert first["created_occurrences"] == 1
    assert first["states"]["done"] == 1
    assert second["created_occurrences"] == 0
    assert store.occurrence_count("test.once") == 1


def test_misfire_skip_records_skipped(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    store.register(_once("test.misfire", 100, misfire="skip"), now=50)
    engine = SchedulerEngine(
        store,
        SchedulerEngineConfig(misfire_grace_seconds=10),
    )
    receipt = engine.tick(now=200)
    assert receipt["states"]["skipped"] == 1
    occurrence = store.list_occurrences("test.misfire")[0]
    assert occurrence.reason == "MISFIRE_SKIP"


def test_operator_policy_routes_to_needs_operator(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    store.register(
        _once("test.operator", 100, approval="require_operator_each_occurrence"),
        now=50,
    )
    receipt = SchedulerEngine(store).tick(now=100)
    assert receipt["states"]["needs_operator"] == 1


def test_overlap_queue_one_allows_only_one_pending(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    schedule, _ = store.register(
        _once("test.queue", 100, approval="auto_run_low_risk", overlap="queue_one"),
        now=50,
    )
    existing_id = stable_occurrence_id(schedule.spec.schedule_id, schedule.version, 90)
    proposal = build_execution_proposal(schedule, occurrence_id=existing_id, scheduled_for=90)
    store.create_occurrence(
        occurrence_id=existing_id,
        schedule=schedule,
        scheduled_for=90,
        state="running",
        reason="DISPATCH_STARTED",
        proposal=proposal,
        now=90,
    )
    receipt = SchedulerEngine(store).tick(now=100)
    assert receipt["states"]["pending"] == 1


def test_tick_recovers_stale_running_without_reexecution(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    schedule, _ = store.register(_once("test.recovery", 500), now=1)
    occurrence_id = stable_occurrence_id(schedule.spec.schedule_id, schedule.version, 100)
    proposal = build_execution_proposal(schedule, occurrence_id=occurrence_id, scheduled_for=100)
    store.create_occurrence(
        occurrence_id=occurrence_id,
        schedule=schedule,
        scheduled_for=100,
        state="running",
        reason="DISPATCH_STARTED",
        proposal=proposal,
        now=100,
    )
    engine = SchedulerEngine(
        store,
        SchedulerEngineConfig(stale_running_after_seconds=50),
    )
    receipt = engine.tick(now=200)
    assert receipt["recovered_stale_running"] == 1
    assert store.list_occurrences("test.recovery")[0].state == "needs_operator"
