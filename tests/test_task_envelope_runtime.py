from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.runner_executors import ExecutionContext
from core.task_envelope_runtime import (
    TaskEnvelopeRuntimeError,
    execute_task_envelope_file,
)


def _write_envelope(path: Path, *, value: str, key: str = "idem-001") -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "skeleton.runner.task_envelope.v1",
                "task_id": "runtime-test",
                "executor_class": "python.entrypoint",
                "target": None,
                "steps": [
                    {
                        "entrypoint": "record",
                        "input": {"state": "ok", "value": value},
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
                "rollback_policy": {"mode": "none"},
                "privacy_class": "private",
                "risk_class": "yellow",
                "approval": {
                    "operator_approved": True,
                    "approval_id": "approval-001",
                    "second_stage_approved": False,
                },
                "idempotency_key": key,
                "evidence_policy": {"public": "aggregate_only"},
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_runtime_reuses_receipt_without_reexecuting(tmp_path: Path) -> None:
    envelope_path = tmp_path / "task.json"
    _write_envelope(envelope_path, value="first")
    calls: list[object] = []

    def record(value: object) -> object:
        calls.append(value)
        return value

    context = ExecutionContext(
        targets={},
        entrypoints={"record": record},
        roots={},
        environment={},
    )
    kwargs = {
        "context": context,
        "evidence_dir": tmp_path / "evidence",
        "idempotency_dir": tmp_path / "idempotency",
    }

    first = execute_task_envelope_file(envelope_path, **kwargs)
    second = execute_task_envelope_file(envelope_path, **kwargs)

    assert first == second
    assert first["status"] == "DONE"
    assert len(calls) == 1
    assert (tmp_path / "evidence" / "runtime-test.json").is_file()
    assert (tmp_path / "idempotency" / "idem-001.json").is_file()


def test_runtime_rejects_idempotency_key_reuse_for_changed_envelope(
    tmp_path: Path,
) -> None:
    envelope_path = tmp_path / "task.json"
    calls: list[object] = []
    context = ExecutionContext(
        targets={},
        entrypoints={"record": lambda value: calls.append(value) or value},
        roots={},
        environment={},
    )
    kwargs = {
        "context": context,
        "idempotency_dir": tmp_path / "idempotency",
    }

    _write_envelope(envelope_path, value="first")
    execute_task_envelope_file(envelope_path, **kwargs)
    _write_envelope(envelope_path, value="changed")

    with pytest.raises(TaskEnvelopeRuntimeError, match="already bound"):
        execute_task_envelope_file(envelope_path, **kwargs)

    assert len(calls) == 1
