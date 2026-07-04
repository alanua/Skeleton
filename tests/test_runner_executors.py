from __future__ import annotations

from pathlib import Path

import pytest

from core.runner_execution_broker import RunnerExecutionError
from core.runner_executors import ExecutionContext, filesystem_executor
from core.task_envelope import parse_task_envelope


def envelope(executor_class: str) -> object:
    return parse_task_envelope(
        {
            "schema": "skeleton.runner.task_envelope.v1",
            "task_id": "executor-test",
            "executor_class": executor_class,
            "target": None,
            "steps": [{"operation": "read_text", "root": "data", "path": "a.txt"}],
            "timeout_seconds": 30,
            "environment_refs": [],
            "expected_assertions": [],
            "rollback_policy": {"mode": "none"},
            "privacy_class": "private",
            "risk_class": "yellow",
            "approval": {
                "operator_approved": True,
                "approval_id": "approval-001",
                "second_stage_approved": False,
            },
            "idempotency_key": "executor-test-v1",
            "evidence_policy": {"public": "aggregate_only"},
        }
    )


def test_filesystem_executor_is_bounded_to_registered_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    context = ExecutionContext(
        targets={},
        entrypoints={},
        roots={"data": root},
        environment={},
    )
    execute = filesystem_executor(context)

    result = execute(
        envelope("filesystem"),
        {"operation": "read_text", "root": "data", "path": "a.txt"},
    )

    assert result.status == "DONE"
    assert result.output == "alpha"


def test_filesystem_executor_rejects_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    context = ExecutionContext(
        targets={},
        entrypoints={},
        roots={"data": root},
        environment={},
    )
    execute = filesystem_executor(context)

    with pytest.raises(RunnerExecutionError, match="outside"):
        execute(
            envelope("filesystem"),
            {"operation": "read_text", "root": "data", "path": "../escape"},
        )
