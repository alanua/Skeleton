from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
import time

from core.scheduler_engine import (
    SchedulerEngine,
    SchedulerEngineConfig,
    production_scheduler_db_path,
)
from core.domain_event_graph import DomainEventGraph
from core.scheduler_models import ScheduleSpec, build_execution_proposal, stable_occurrence_id
from core.scheduler_store import SchedulerStore
from core.shared_dispatch import (
    PRIVACY_PUBLIC_SAFE,
    SharedDispatcher,
    SharedDispatchRequest,
    SharedDispatchResult,
)


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


def _loop_once(schedule_id: str, once_at: int, task_packet: dict[str, object], **payload):
    merged_payload = {
        "privacy_boundary": PRIVACY_PUBLIC_SAFE,
        "bounded": True,
        "approved_capabilities": ["loop:state_write"],
        "requested_capabilities": ["loop:state_write"],
        "task_packet": task_packet,
    }
    merged_payload.update(payload)
    return ScheduleSpec.from_mapping(
        {
            "schema": "skeleton.schedule.v1",
            "schedule_id": schedule_id,
            "trigger_kind": "once",
            "cron_expression": None,
            "once_at": once_at,
            "timezone": "UTC",
            "route_type": "loop",
            "route_id": "loop_engine_packet",
            "approval_policy": "auto_run_low_risk",
            "overlap_policy": "queue_one",
            "misfire_policy": "run_once",
            "payload": merged_payload,
        }
    )


def _loop_packet(action: str, **updates: object) -> dict[str, object]:
    packet: dict[str, object] = {
        "schema": "skeleton.loop_runner_packet.v1",
        "action": action,
        "task_id": "task-1",
        "run_id": "run-1",
        "recorded_at": 1,
        "public_safe": True,
        "no_secrets": True,
        "no_runtime_mutation": True,
        "authority_boundary": {
            "operational_state_write": True,
            "external_side_effects_allowed": False,
            "runtime_mutation_allowed": False,
        },
    }
    if action == "step":
        packet.update({"event": "PREPARED", "expected_version": 0})
    packet.update(updates)
    return packet


def _recovery_once(schedule_id: str, once_at: int, recovery_packet: dict[str, object]):
    return ScheduleSpec.from_mapping(
        {
            "schema": "skeleton.schedule.v1",
            "schedule_id": schedule_id,
            "trigger_kind": "once",
            "cron_expression": None,
            "once_at": once_at,
            "timezone": "UTC",
            "route_type": "workflow",
            "route_id": "control_recovery",
            "approval_policy": "auto_run_low_risk",
            "overlap_policy": "queue_one",
            "misfire_policy": "run_once",
            "payload": {
                "privacy_boundary": PRIVACY_PUBLIC_SAFE,
                "bounded": True,
                "approved_capabilities": ["control:recovery"],
                "requested_capabilities": ["control:recovery"],
                "recovery_packet": recovery_packet,
            },
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
    assert receipt["retried_stale_running"] == 1
    assert store.list_occurrences("test.recovery")[0].state == "pending"


def test_live_heartbeat_prevents_reclaim_during_long_dispatch(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    schedule, _ = store.register(_loop_once("test.heartbeat", 100, _loop_packet("create")), now=50)
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
    started = threading.Event()
    release = threading.Event()

    class LongDispatcher:
        def dispatch(self, request: SharedDispatchRequest) -> SharedDispatchResult:
            started.set()
            assert release.wait(timeout=5)
            return SharedDispatchResult(
                "done",
                "SYNTHETIC_DONE",
                {
                    "status": "DONE",
                    "accepted": True,
                    "reason": "SYNTHETIC_DONE",
                    "public_safe": True,
                    "external_side_effects_executed": False,
                },
                "synthetic:done",
            )

    clock_values = iter(range(101, 120))
    engine = SchedulerEngine(
        store,
        SchedulerEngineConfig(
            initial_lease_seconds=2,
            heartbeat_interval_seconds=1,
            stale_running_after_seconds=2,
        ),
        clock=lambda: next(clock_values),
    )
    result: dict[str, object] = {}

    def dispatch() -> None:
        result.update(engine.dispatch_pending(dispatcher=LongDispatcher(), now=100))

    thread = threading.Thread(target=dispatch)
    thread.start()
    assert started.wait(timeout=5)
    deadline = time.time() + 5
    while time.time() < deadline:
        with sqlite3.connect(store.db_path) as connection:
            row = connection.execute(
                "SELECT lease_expires_at FROM occurrences WHERE occurrence_id = ?",
                (occurrence_id,),
            ).fetchone()
        if row is not None and int(row[0]) > 103:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("heartbeat did not renew lease")

    recovered = SchedulerEngine(
        store,
        SchedulerEngineConfig(
            initial_lease_seconds=2,
            heartbeat_interval_seconds=1,
            stale_running_after_seconds=2,
        ),
    ).tick(now=103)

    assert recovered["recovered_stale_running"] == 0
    assert store.get_occurrence(occurrence_id).state == "running"  # type: ignore[union-attr]
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert result["done"] == 1
    assert store.get_occurrence(occurrence_id).state == "done"  # type: ignore[union-attr]


def test_stopped_heartbeat_crash_becomes_recoverable_after_lease_expiry(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    schedule, _ = store.register(_loop_once("test.crash", 100, _loop_packet("create")), now=50)
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
    claimed = store.claim_next_pending(now=100, owner="worker-a", lease_seconds=2)

    early = store.recover_stale_running(now=101, stale_after_seconds=2, max_attempts=2)
    expired = store.recover_stale_running(now=102, stale_after_seconds=2, max_attempts=2)

    assert claimed is not None
    assert early == {"retried": 0, "needs_operator": 0}
    assert expired["retried"] == 1
    assert store.get_occurrence(occurrence_id).state == "pending"  # type: ignore[union-attr]


def test_crash_before_receipt_reclaims_with_bounded_retry(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    schedule, _ = store.register(_loop_once("test.before.receipt", 100, _loop_packet("create")), now=50)
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
    store.claim_next_pending(now=100, owner="worker-a", lease_seconds=1)

    recovered = store.recover_stale_running(now=101, stale_after_seconds=1, max_attempts=1)

    assert recovered["needs_operator"] == 1
    record = store.get_occurrence(occurrence_id)
    assert record is not None
    assert record.state == "needs_operator"
    assert record.reason == "STALE_RUNNING_RECOVERY_EXHAUSTED"


def test_crash_after_successful_receipt_finalizes_without_dispatcher_call(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    schedule, _ = store.register(_loop_once("test.after.receipt", 100, _loop_packet("create")), now=50)
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
    claimed = store.claim_next_pending(now=100, owner="worker-a", lease_seconds=1)
    assert claimed is not None
    store.record_dispatch_receipt(
        occurrence_id=occurrence_id,
        attempt=claimed.attempt,
        idempotency_key=claimed.idempotency_key or "",
        status="done",
        reason="SYNTHETIC_DONE",
        evidence_ref="synthetic:done",
        result={"status": "DONE", "external_side_effects_executed": False},
        now=100,
    )

    recovered = SchedulerEngine(
        store,
        SchedulerEngineConfig(stale_running_after_seconds=1),
    ).tick(now=101)

    assert recovered["recovered_stale_running"] == 1
    assert recovered["finalized_stale_running"] == 1
    assert recovered["dispatch"]["claimed"] == 0
    assert store.get_occurrence(occurrence_id).state == "done"  # type: ignore[union-attr]


def test_ambiguous_mutating_receipt_needs_operator_without_replay(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    schedule, _ = store.register(_loop_once("test.ambiguous", 100, _loop_packet("create")), now=50)
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
    claimed = store.claim_next_pending(now=100, owner="worker-a", lease_seconds=1)
    assert claimed is not None
    store.record_dispatch_receipt(
        occurrence_id=occurrence_id,
        attempt=claimed.attempt,
        idempotency_key=claimed.idempotency_key or "",
        status="failed",
        reason="AMBIGUOUS_RESULT",
        evidence_ref="synthetic:ambiguous",
        result={
            "status": "UNKNOWN",
            "external_side_effects_executed": True,
        },
        now=100,
    )

    recovered = SchedulerEngine(
        store,
        SchedulerEngineConfig(stale_running_after_seconds=1),
    ).tick(now=101)

    assert recovered["stale_running_needs_operator"] == 1
    assert recovered["dispatch"]["claimed"] == 0
    record = store.get_occurrence(occurrence_id)
    assert record is not None
    assert record.state == "needs_operator"
    assert record.reason == "AMBIGUOUS_MUTATING_RECEIPT"


def test_live_ambiguous_mutating_result_needs_operator_without_retry(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    schedule, _ = store.register(_loop_once("test.live.ambiguous", 100, _loop_packet("create")), now=50)
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

    class AmbiguousDispatcher:
        def dispatch(self, request: SharedDispatchRequest) -> SharedDispatchResult:
            return SharedDispatchResult(
                "failed",
                "AMBIGUOUS_RESULT",
                {
                    "status": "UNKNOWN",
                    "accepted": False,
                    "reason": "AMBIGUOUS_RESULT",
                    "external_side_effects_executed": True,
                },
                "synthetic:ambiguous",
                retryable=True,
            )

    receipt = SchedulerEngine(store).dispatch_pending(
        dispatcher=AmbiguousDispatcher(), now=100
    )

    assert receipt["needs_operator"] == 1
    assert receipt["retried"] == 0
    record = store.get_occurrence(occurrence_id)
    assert record is not None
    assert record.state == "needs_operator"
    assert record.reason == "AMBIGUOUS_MUTATING_RECEIPT"


def test_concurrent_claim_attempts_yield_single_owner_attempt(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    schedule, _ = store.register(_loop_once("test.concurrent", 100, _loop_packet("create")), now=50)
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

    def claim(owner: str):
        local = SchedulerStore(store.db_path)
        return local.claim_next_pending(now=100, owner=owner, lease_seconds=30)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(claim, [f"owner-{index}" for index in range(8)]))

    claimed = [item for item in results if item is not None]
    assert len(claimed) == 1
    assert claimed[0].attempt == 1
    assert store.get_occurrence(occurrence_id).state == "running"  # type: ignore[union-attr]


def test_due_schedule_dispatches_to_loop_and_finishes_without_manual_nudge(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    loop_db = tmp_path / "loop.sqlite3"
    store.initialize()
    store.register(_loop_once("test.loop", 100, _loop_packet("create")), now=50)

    receipt = SchedulerEngine(store).tick(
        now=100,
        dispatcher=SharedDispatcher.for_loop_engine(loop_state_db_path=str(loop_db)),
    )

    occurrences = store.list_occurrences("test.loop")
    assert receipt["dispatch"]["claimed"] == 1
    assert receipt["dispatch"]["done"] == 1
    assert occurrences[0].state == "done"
    assert occurrences[0].attempt == 1
    receipts = store.list_dispatch_receipts(occurrences[0].occurrence_id)
    assert len(receipts) == 1
    assert receipts[0]["idempotency_key"].endswith(":attempt:1")


def test_synthetic_multi_step_chain_activates_second_step(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    loop_db = tmp_path / "loop.sqlite3"
    store.initialize()
    steps = [
        _loop_packet("create"),
        _loop_packet("step", recorded_at=2, event="PREPARED", expected_version=0),
    ]
    store.register(
        _loop_once(
            "test.chain",
            100,
            steps[0],
            deterministic_workflow={"steps": steps, "index": 0},
        ),
        now=50,
    )
    engine = SchedulerEngine(store)
    dispatcher = SharedDispatcher.for_loop_engine(loop_state_db_path=str(loop_db))

    first = engine.tick(now=100, dispatcher=dispatcher)
    occurrences = store.list_occurrences("test.chain")
    assert first["dispatch"]["continued"] == 1
    assert first["dispatch"]["done"] == 2
    assert [item.state for item in occurrences] == ["done", "done"]
    assert occurrences[1].parent_occurrence_id == occurrences[0].occurrence_id


def test_recoverable_failure_retries_and_then_succeeds(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    loop_db = tmp_path / "loop.sqlite3"
    store.initialize()
    store.register(
        _loop_once(
            "test.retry",
            100,
            _loop_packet("step", run_id="missing-run", recorded_at=1),
        ),
        now=50,
    )
    engine = SchedulerEngine(store)
    dispatcher = SharedDispatcher.for_loop_engine(loop_state_db_path=str(loop_db))

    first = engine.tick(now=100, dispatcher=dispatcher)
    occurrences = store.list_occurrences("test.retry")
    assert first["dispatch"]["retried"] == 1
    assert occurrences[0].state == "pending"

    run_id = occurrences[0].proposal["payload"]["task_packet"]["run_id"]
    create = dict(occurrences[0].proposal["payload"]["task_packet"])
    create.update({"action": "create", "recorded_at": 2})
    create.pop("event", None)
    create.pop("expected_version", None)
    dispatcher.dispatch(
        SharedDispatchRequest(
            occurrence_id="manual",
            route_type="loop",
            route_id="loop_engine_packet",
            payload={
                "privacy_boundary": PRIVACY_PUBLIC_SAFE,
                "bounded": True,
                "approved_capabilities": ["loop:state_write"],
                "requested_capabilities": ["loop:state_write"],
                "task_packet": create,
            },
            attempt=1,
            idempotency_key="manual:attempt:1",
        )
    )
    second = engine.tick(now=101, dispatcher=dispatcher)

    assert run_id == "missing-run"
    assert second["dispatch"]["done"] == 1
    assert store.list_occurrences("test.retry")[0].state == "done"


def test_waiting_dependency_resumes_after_dependency_done(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    loop_db = tmp_path / "loop.sqlite3"
    store.initialize()
    dependency, _ = store.register(_loop_once("test.dep", 100, _loop_packet("create")), now=50)
    dependent, _ = store.register(
        _loop_once("test.wait", 100, _loop_packet("create", run_id="run-2"), wait_for="missing"),
        now=50,
    )
    dep_id = stable_occurrence_id(dependency.spec.schedule_id, dependency.version, 100)
    wait_id = stable_occurrence_id(dependent.spec.schedule_id, dependent.version, 100)
    wait_proposal = build_execution_proposal(dependent, occurrence_id=wait_id, scheduled_for=100)
    wait_proposal["payload"]["wait_for"] = dep_id
    store.create_occurrence(
        occurrence_id=wait_id,
        schedule=dependent,
        scheduled_for=100,
        state="pending",
        reason="DISPATCH_REQUIRED",
        proposal=wait_proposal,
        now=100,
    )
    engine = SchedulerEngine(store)
    dispatcher = SharedDispatcher.for_loop_engine(loop_state_db_path=str(loop_db))

    first = engine.dispatch_pending(dispatcher=dispatcher, now=100)
    assert first["waiting_dependency"] == 1
    dep_proposal = build_execution_proposal(dependency, occurrence_id=dep_id, scheduled_for=100)
    store.create_occurrence(
        occurrence_id=dep_id,
        schedule=dependency,
        scheduled_for=100,
        state="done",
        reason="DISPATCH_DONE",
        proposal=dep_proposal,
        now=101,
    )
    resumed = engine.tick(now=102, dispatcher=dispatcher)

    assert resumed["resumed_waiting_dependencies"] == 1
    assert store.get_occurrence(wait_id).state == "done"  # type: ignore[union-attr]


def test_waiting_dependency_records_domain_graph_hook(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    loop_db = tmp_path / "loop.sqlite3"
    graph = DomainEventGraph()
    store.initialize()
    dependency, _ = store.register(_loop_once("test.dep.graph", 100, _loop_packet("create")), now=50)
    dependent, _ = store.register(
        _loop_once("test.wait.graph", 100, _loop_packet("create", run_id="run-graph"), wait_for="missing"),
        now=50,
    )
    dep_id = stable_occurrence_id(dependency.spec.schedule_id, dependency.version, 100)
    wait_id = stable_occurrence_id(dependent.spec.schedule_id, dependent.version, 100)
    wait_proposal = build_execution_proposal(dependent, occurrence_id=wait_id, scheduled_for=100)
    wait_proposal["payload"]["wait_for"] = dep_id
    store.create_occurrence(
        occurrence_id=wait_id,
        schedule=dependent,
        scheduled_for=100,
        state="pending",
        reason="DISPATCH_REQUIRED",
        proposal=wait_proposal,
        now=100,
    )
    dispatcher = SharedDispatcher.for_loop_engine(loop_state_db_path=str(loop_db))
    engine = SchedulerEngine(store, domain_event_graph=graph)

    first = engine.dispatch_pending(dispatcher=dispatcher, now=100)
    second = engine.dispatch_pending(dispatcher=dispatcher, now=100)

    assert first["waiting_dependency"] == 1
    assert second["claimed"] == 0
    assert graph.summary()["aggregate_counts"]["event_count"] == 1


def test_recovery_route_requeues_blocked_consumer_after_canary(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    recovery, _ = store.register(
        _recovery_once(
            "test.recovery.route",
            100,
            {
                "schema": "skeleton.control_recovery.v1",
                "failure_class": "CODEGEN_RUNTIME_UNHEALTHY",
                "failure_key": "control:p0-consumer",
            },
        ),
        now=50,
    )
    consumer, _ = store.register(
        _loop_once("test.consumer", 100, _loop_packet("create", run_id="run-consumer")),
        now=50,
    )
    recovery_id = stable_occurrence_id(recovery.spec.schedule_id, recovery.version, 100)
    consumer_id = stable_occurrence_id(consumer.spec.schedule_id, consumer.version, 100)
    consumer_proposal = build_execution_proposal(
        consumer, occurrence_id=consumer_id, scheduled_for=100
    )
    consumer_proposal["payload"]["wait_for"] = recovery_id
    store.create_occurrence(
        occurrence_id=consumer_id,
        schedule=consumer,
        scheduled_for=100,
        state="waiting_dependency",
        reason="WAITING_RECOVERY",
        proposal=consumer_proposal,
        now=99,
    )
    dispatcher = SharedDispatcher.for_control_recovery(
        recovery_db_path=str(tmp_path / "recovery.sqlite3"),
        action_executor=lambda _action: "DONE: ok\nsuccess_criteria=met",
        canary_executor=lambda _canary: True,
        now=100,
    )

    first = SchedulerEngine(store).tick(now=100, dispatcher=dispatcher)

    assert first["dispatch"]["done"] == 1
    assert first["resumed_waiting_dependencies"] == 0
    second = SchedulerEngine(store).tick(
        now=101,
        dispatcher=SharedDispatcher.for_loop_engine(
            loop_state_db_path=str(tmp_path / "loop.sqlite3")
        ),
    )
    assert second["resumed_waiting_dependencies"] == 1
    assert store.get_occurrence(consumer_id).state == "done"  # type: ignore[union-attr]


def test_production_scheduler_db_is_fixed_agent_local() -> None:
    path = production_scheduler_db_path()
    assert path.as_posix() == (
        "/home/agent/.local/state/skeleton-runner/scheduler/scheduler.sqlite3"
    )
    rendered = str(path)
    assert "/var/lib" not in rendered
    assert "/tmp" not in rendered
    assert "/.codex/" not in rendered
