from __future__ import annotations

from core.scheduler_models import ScheduleSpec
from core.scheduler_store import SchedulerStore
from core.shared_dispatch import PRIVACY_PUBLIC_SAFE
from scripts.scheduler_runtime import run_scheduler_tick


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
