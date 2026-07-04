from __future__ import annotations

import pytest

from core.governed_task_executor import execute_governed_task
from core.runner_executors import ExecutionContext
from core.task_envelope import parse_task_envelope
from core.task_risk_policy import TaskRiskPolicyError


def envelope(*, risk: str = "yellow", rollback: dict[str, object]) -> object:
    approved = risk != "green"
    return parse_task_envelope(
        {
            "schema": "skeleton.runner.task_envelope.v1",
            "task_id": "rollback-test",
            "executor_class": "python.entrypoint",
            "target": None,
            "steps": [
                {
                    "entrypoint": "run",
                    "input": {"state": "wrong"},
                }
            ],
            "timeout_seconds": 30,
            "environment_refs": [],
            "expected_assertions": [
                {
                    "kind": "json_path_eq",
                    "path": "state",
                    "value": "ok",
                }
            ],
            "rollback_policy": rollback,
            "privacy_class": "private",
            "risk_class": risk,
            "approval": {
                "operator_approved": approved,
                "approval_id": "approval-001" if approved else None,
                "second_stage_approved": risk == "red",
            },
            "idempotency_key": "rollback-test-v1",
            "evidence_policy": {"public": "aggregate_only"},
        }
    )


def test_blocked_task_executes_generic_rollback_steps() -> None:
    calls: list[str] = []
    context = ExecutionContext(
        targets={},
        entrypoints={
            "run": lambda value: calls.append("run") or value,
            "undo": lambda value: calls.append("undo") or value,
        },
        roots={},
        environment={},
    )
    value = envelope(
        rollback={
            "mode": "steps",
            "steps": [
                {
                    "executor_class": "python.entrypoint",
                    "entrypoint": "undo",
                    "input": {"state": "restored"},
                }
            ],
        }
    )

    result = execute_governed_task(value, context=context)

    assert result["status"] == "BLOCKED"
    assert result["public_receipt"]["rollback_status"] == "DONE"
    assert result["public_receipt"]["rollback_step_count"] == 1
    assert result["private_evidence"]["rollback"]["status"] == "DONE"
    assert calls == ["run", "undo"]


def test_red_task_requires_rollback_or_irreversibility() -> None:
    value = envelope(risk="red", rollback={"mode": "none"})

    with pytest.raises(TaskRiskPolicyError, match="red tasks require"):
        execute_governed_task(
            value,
            context=ExecutionContext({}, {"run": lambda value: value}, {}, {}),
        )


def test_red_irreversible_task_requires_reason() -> None:
    value = envelope(risk="red", rollback={"mode": "irreversible"})

    with pytest.raises(TaskRiskPolicyError, match="bounded reason"):
        execute_governed_task(
            value,
            context=ExecutionContext({}, {"run": lambda value: value}, {}, {}),
        )
