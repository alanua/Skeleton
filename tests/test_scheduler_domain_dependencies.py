from __future__ import annotations

from pathlib import Path

from core.domain_event_graph import dependency_state
from core.loop_controller import LoopPolicy, LoopState
from core.loop_engine import LoopEngine
from core.loop_state_store import LoopStateStore
from core.scheduler_engine import SchedulerEngine
from core.scheduler_models import ScheduleSpec
from core.scheduler_store import SchedulerStore
from core.shared_dispatch import PRIVACY_PUBLIC_SAFE, SharedDispatcher


def _loop_packet() -> dict[str, object]:
    return {
        "schema": "skeleton.loop_runner_packet.v1",
        "action": "create",
        "task_id": "task-graph",
        "run_id": "run-graph",
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


def _schedule(graph_dependency: dict[str, object]) -> ScheduleSpec:
    return ScheduleSpec.from_mapping(
        {
            "schema": "skeleton.schedule.v1",
            "schedule_id": "test.graph_dependency",
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
                "task_packet": _loop_packet(),
                "graph_dependency": graph_dependency,
            },
        }
    )


def test_scheduler_waits_when_graph_dependency_is_unverified(tmp_path: Path) -> None:
    source_ref = "runner:runner_task:runner_200"
    target_ref = "development:goal:goal_200"
    graph_state = dependency_state(
        [
            {
                "edge_id": "edge_unverified",
                "source_ref": source_ref,
                "target_ref": target_ref,
                "edge_kind": "depends_on",
                "verified": False,
            }
        ],
        source_ref=source_ref,
        target_ref=target_ref,
    )
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    store.register(_schedule({"source_ref": source_ref, "target_ref": target_ref}), now=50)

    receipt = SchedulerEngine(store, dependency_resolver=lambda _payload: graph_state).tick(
        now=100,
        dispatcher=SharedDispatcher.for_loop_engine(loop_state_db_path=str(tmp_path / "loop.sqlite3")),
    )

    occurrence = store.list_occurrences("test.graph_dependency")[0]
    assert receipt["dispatch"]["waiting_dependency"] == 1
    assert occurrence.state == "waiting_dependency"
    assert store.list_dispatch_receipts(occurrence.occurrence_id)[0]["result"]["destructive_actions_allowed"] is False


def test_scheduler_dispatches_when_graph_dependency_is_verified(tmp_path: Path) -> None:
    source_ref = "runner:runner_task:runner_201"
    target_ref = "development:goal:goal_201"
    graph_state = dependency_state(
        [
            {
                "edge_id": "edge_verified",
                "source_ref": source_ref,
                "target_ref": target_ref,
                "edge_kind": "depends_on",
                "verified": True,
            }
        ],
        source_ref=source_ref,
        target_ref=target_ref,
    )
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    store.register(_schedule({"source_ref": source_ref, "target_ref": target_ref}), now=50)

    receipt = SchedulerEngine(store, dependency_resolver=lambda _payload: graph_state).tick(
        now=100,
        dispatcher=SharedDispatcher.for_loop_engine(loop_state_db_path=str(tmp_path / "loop.sqlite3")),
    )

    assert receipt["dispatch"]["done"] == 1
    assert store.list_occurrences("test.graph_dependency")[0].state == "done"


def test_loop_creation_hook_blocks_unverified_graph_dependency(tmp_path: Path) -> None:
    store = LoopStateStore(tmp_path / "loop.sqlite3")
    store.initialize()
    engine = LoopEngine(
        store,
        policy=LoopPolicy(),
        dependency_resolver=lambda _payload: {"verified": False},
    )

    run = engine.create(
        run_id="run-blocked",
        task_id="task-blocked",
        recorded_at=1,
        dependency={"source_ref": "runner:runner_task:runner_202", "target_ref": "development:goal:goal_202"},
    )

    assert run.context.state is LoopState.BLOCKED
