from __future__ import annotations

from core.runner_execution_broker import RunnerExecutionBroker, StepResult
from core.task_envelope import parse_task_envelope


def make_envelope(executor_class: str, *, steps: list[dict[str, object]] | None = None) -> object:
    return parse_task_envelope(
        {
            "schema": "skeleton.runner.task_envelope.v1",
            "task_id": "task-001",
            "executor_class": executor_class,
            "target": "registered-target",
            "steps": steps or [{"value": "ok"}],
            "timeout_seconds": 30,
            "environment_refs": [],
            "expected_assertions": [
                {"kind": "json_path_eq", "path": "state", "value": "ok"}
            ],
            "rollback_policy": {"mode": "none"},
            "privacy_class": "private",
            "risk_class": "yellow",
            "approval": {
                "operator_approved": True,
                "approval_id": "approval-001",
                "second_stage_approved": False,
            },
            "idempotency_key": "task-001-v1",
            "evidence_policy": {"public": "aggregate_only"},
        }
    )


def test_generic_executor_dispatches_without_action_handler() -> None:
    seen: list[dict[str, object]] = []

    def executor(_envelope: object, step: dict[str, object]) -> StepResult:
        seen.append(step)
        return StepResult(
            executor_class="network.http",
            status="DONE",
            status_code=200,
            output={"state": "ok"},
        )

    broker = RunnerExecutionBroker({"network.http": executor})
    result = broker.execute(make_envelope("network.http"))

    assert result["status"] == "DONE"
    assert seen == [{"value": "ok"}]
    assert result["assertions"] == [{"kind": "json_path_eq", "passed": True}]


def test_failed_assertion_blocks_result() -> None:
    def executor(_envelope: object, _step: dict[str, object]) -> StepResult:
        return StepResult(
            executor_class="network.http",
            status="DONE",
            status_code=200,
            output={"state": "wrong"},
        )

    broker = RunnerExecutionBroker({"network.http": executor})
    result = broker.execute(make_envelope("network.http"))

    assert result["status"] == "BLOCKED"


def test_executor_failure_stops_following_steps() -> None:
    calls = 0

    def executor(_envelope: object, _step: dict[str, object]) -> StepResult:
        nonlocal calls
        calls += 1
        return StepResult(executor_class="local.process", status="BLOCKED", exit_code=1)

    broker = RunnerExecutionBroker({"local.process": executor})
    result = broker.execute(
        make_envelope(
            "local.process",
            steps=[{"argv": ["false"]}, {"argv": ["echo", "must-not-run"]}],
        )
    )

    assert calls == 1
    assert result["status"] == "BLOCKED"
