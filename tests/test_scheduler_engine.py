from core.scheduler_engine import (
    SchedulerEngine,
    SchedulerEngineConfig,
    production_scheduler_db_path,
)
from core.scheduler_models import ScheduleSpec, build_execution_proposal, stable_occurrence_id
from core.scheduler_store import SchedulerStore
from core.shared_dispatch import PRIVACY_PUBLIC_SAFE, SharedDispatcher, SharedDispatchRequest


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


class _CountingDispatcher:
    def __init__(self, status: str = "done") -> None:
        self.status = status
        self.calls: list[SharedDispatchRequest] = []

    def dispatch(self, request: SharedDispatchRequest):
        self.calls.append(request)
        return type(
            "DispatchResult",
            (),
            {
                "status": self.status,
                "reason": "TEST_DISPATCH",
                "receipt": {
                    "schema": "skeleton.scheduler_dispatch_receipt.v1",
                    "status": "DONE" if self.status == "done" else "BLOCKED",
                    "accepted": self.status == "done",
                    "reason": "TEST_DISPATCH",
                    "public_safe": True,
                    "external_side_effects_executed": False,
                },
                "evidence_ref": "test:dispatch",
                "retryable": False,
                "waiting_dependency": None,
                "next_step": None,
            },
        )()


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


def test_crash_after_claim_before_receipt_reclaims_after_restart(tmp_path) -> None:
    db_path = tmp_path / "scheduler.sqlite3"
    store = SchedulerStore(db_path)
    store.initialize()
    store.register(_loop_once("test.claim.crash", 100, _loop_packet("create")), now=50)
    SchedulerEngine(store).tick(now=100)
    claimed = store.claim_next_pending(now=101, lease_seconds=10)
    assert claimed is not None
    assert claimed.attempt == 1

    restarted = SchedulerStore(db_path)
    dispatcher = _CountingDispatcher()
    receipt = SchedulerEngine(
        restarted,
        SchedulerEngineConfig(stale_running_after_seconds=10, lease_seconds=10),
    ).tick(now=112, dispatcher=dispatcher)  # type: ignore[arg-type]

    occurrence = restarted.list_occurrences("test.claim.crash")[0]
    assert receipt["retried_stale_running"] == 1
    assert receipt["dispatch"]["done"] == 1
    assert occurrence.state == "done"
    assert occurrence.attempt == 2
    assert len(dispatcher.calls) == 1


def test_crash_after_success_receipt_finalizes_without_replay(tmp_path) -> None:
    db_path = tmp_path / "scheduler.sqlite3"
    store = SchedulerStore(db_path)
    store.initialize()
    store.register(_loop_once("test.receipt.crash", 100, _loop_packet("create")), now=50)
    SchedulerEngine(store).tick(now=100)
    occurrence = store.claim_next_pending(now=101, lease_seconds=10)
    assert occurrence is not None
    store.record_dispatch_receipt(
        occurrence_id=occurrence.occurrence_id,
        attempt=occurrence.attempt,
        idempotency_key=occurrence.idempotency_key or "",
        status="done",
        reason="TEST_DONE",
        evidence_ref="test:done",
        result={
            "schema": "skeleton.scheduler_dispatch_receipt.v1",
            "status": "DONE",
            "accepted": True,
            "reason": "TEST_DONE",
            "public_safe": True,
            "external_side_effects_executed": False,
        },
        now=102,
    )

    restarted = SchedulerStore(db_path)
    dispatcher = _CountingDispatcher()
    receipt = SchedulerEngine(
        restarted,
        SchedulerEngineConfig(stale_running_after_seconds=10, lease_seconds=10),
    ).tick(now=112, dispatcher=dispatcher)  # type: ignore[arg-type]

    occurrence = restarted.list_occurrences("test.receipt.crash")[0]
    assert receipt["finished_stale_running"] == 1
    assert receipt["dispatch"]["claimed"] == 0
    assert occurrence.state == "done"
    assert occurrence.attempt == 1
    assert dispatcher.calls == []


def test_ambiguous_protected_receipt_needs_operator_after_restart(tmp_path) -> None:
    db_path = tmp_path / "scheduler.sqlite3"
    store = SchedulerStore(db_path)
    store.initialize()
    store.register(
        _recovery_once(
            "test.ambiguous.protected",
            100,
            {
                "schema": "skeleton.control_recovery.v1",
                "failure_class": "CODEGEN_RUNTIME_UNHEALTHY",
                "failure_key": "control:ambiguous",
            },
        ),
        now=50,
    )
    SchedulerEngine(store).tick(now=100)
    occurrence = store.claim_next_pending(now=101, lease_seconds=10)
    assert occurrence is not None
    store.record_dispatch_receipt(
        occurrence_id=occurrence.occurrence_id,
        attempt=occurrence.attempt,
        idempotency_key=occurrence.idempotency_key or "",
        status="failed",
        reason="DISPATCH_HANDLER_RAISED",
        evidence_ref="test:ambiguous",
        result={
            "schema": "skeleton.scheduler_dispatch_receipt.v1",
            "status": "BLOCKED",
            "accepted": False,
            "reason": "DISPATCH_HANDLER_RAISED",
            "route_receipt": {
                "status": "WAITING_RECOVERY",
                "external_side_effects_executed": True,
            },
            "public_safe": True,
            "external_side_effects_executed": True,
        },
        now=102,
    )

    restarted = SchedulerStore(db_path)
    dispatcher = _CountingDispatcher()
    receipt = SchedulerEngine(
        restarted,
        SchedulerEngineConfig(stale_running_after_seconds=10, lease_seconds=10),
    ).tick(now=112, dispatcher=dispatcher)  # type: ignore[arg-type]

    occurrence = restarted.list_occurrences("test.ambiguous.protected")[0]
    assert receipt["stale_running_needs_operator"] == 1
    assert occurrence.state == "needs_operator"
    assert occurrence.reason == "AMBIGUOUS_DISPATCH_RECEIPT_NEEDS_OPERATOR"
    assert dispatcher.calls == []


def test_duplicate_claims_and_heartbeat_reconciliation_are_idempotent(tmp_path) -> None:
    db_path = tmp_path / "scheduler.sqlite3"
    store = SchedulerStore(db_path)
    store.initialize()
    store.register(_loop_once("test.lease", 100, _loop_packet("create")), now=50)
    SchedulerEngine(store).tick(now=100)

    first = SchedulerStore(db_path).claim_next_pending(
        now=101, lease_seconds=10, owner="worker-a"
    )
    second = SchedulerStore(db_path).claim_next_pending(
        now=101, lease_seconds=10, owner="worker-b"
    )
    assert first is not None
    assert second is None
    assert SchedulerStore(db_path).refresh_heartbeat(
        occurrence_id=first.occurrence_id,
        owner="worker-a",
        now=108,
        lease_seconds=20,
    )

    live = SchedulerEngine(
        SchedulerStore(db_path),
        SchedulerEngineConfig(stale_running_after_seconds=10, lease_seconds=10),
    ).tick(now=125, dispatcher=_CountingDispatcher())  # type: ignore[arg-type]
    assert live["recovered_stale_running"] == 0
    assert SchedulerStore(db_path).list_occurrences("test.lease")[0].state == "running"

    expired = SchedulerEngine(
        SchedulerStore(db_path),
        SchedulerEngineConfig(stale_running_after_seconds=10, lease_seconds=10),
    ).tick(now=129)
    occurrence = SchedulerStore(db_path).list_occurrences("test.lease")[0]
    assert expired["retried_stale_running"] == 1
    assert occurrence.state == "pending"
    assert occurrence.attempt == 1


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
