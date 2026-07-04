from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.governed_runtime import (
    GovernedRuntimeError,
    execute_governed_envelope_file,
)
from core.runner_execution_broker import RunnerExecutionError
from core.runner_executors import ExecutionContext


def write_entrypoint_envelope(
    path: Path,
    *,
    detail: str,
    key: str = "governed-001",
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "skeleton.runner.task_envelope.v1",
                "task_id": "governed-test",
                "executor_class": "python.entrypoint",
                "target": None,
                "steps": [
                    {
                        "entrypoint": "record",
                        "input": {"state": "ok", "detail": detail},
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


def test_governed_runtime_returns_receipt_and_keeps_evidence_private(
    tmp_path: Path,
) -> None:
    envelope = tmp_path / "task.json"
    write_entrypoint_envelope(envelope, detail="local-only")
    calls: list[object] = []
    context = ExecutionContext(
        targets={},
        entrypoints={"record": lambda value: calls.append(value) or value},
        roots={},
        environment={},
    )

    receipt = execute_governed_envelope_file(
        envelope,
        context=context,
        evidence_dir=tmp_path / "evidence",
        idempotency_dir=tmp_path / "idempotency",
    )

    assert receipt["status"] == "DONE"
    assert "local-only" not in json.dumps(receipt)
    evidence_path = tmp_path / "evidence" / "governed-test.json"
    assert "local-only" in evidence_path.read_text(encoding="utf-8")
    assert evidence_path.stat().st_mode & 0o077 == 0
    assert len(calls) == 1


def test_governed_runtime_does_not_repeat_side_effects(tmp_path: Path) -> None:
    envelope = tmp_path / "task.json"
    write_entrypoint_envelope(envelope, detail="first")
    calls: list[object] = []
    context = ExecutionContext(
        targets={},
        entrypoints={"record": lambda value: calls.append(value) or value},
        roots={},
        environment={},
    )
    kwargs = {
        "context": context,
        "evidence_dir": tmp_path / "evidence",
        "idempotency_dir": tmp_path / "idempotency",
    }

    first = execute_governed_envelope_file(envelope, **kwargs)
    second = execute_governed_envelope_file(envelope, **kwargs)

    assert first == second
    assert len(calls) == 1


def test_governed_runtime_rejects_changed_envelope_for_same_key(
    tmp_path: Path,
) -> None:
    envelope = tmp_path / "task.json"
    context = ExecutionContext(
        targets={},
        entrypoints={"record": lambda value: value},
        roots={},
        environment={},
    )
    kwargs = {
        "context": context,
        "evidence_dir": tmp_path / "evidence",
        "idempotency_dir": tmp_path / "idempotency",
    }

    write_entrypoint_envelope(envelope, detail="first")
    execute_governed_envelope_file(envelope, **kwargs)
    write_entrypoint_envelope(envelope, detail="changed")

    with pytest.raises(GovernedRuntimeError, match="another envelope"):
        execute_governed_envelope_file(envelope, **kwargs)


def test_secure_broker_does_not_register_unbounded_http(tmp_path: Path) -> None:
    envelope = tmp_path / "task.json"
    envelope.write_text(
        json.dumps(
            {
                "schema": "skeleton.runner.task_envelope.v1",
                "task_id": "http-test",
                "executor_class": "network.http",
                "target": None,
                "steps": [{"method": "GET", "url": "https://example.test"}],
                "timeout_seconds": 30,
                "environment_refs": [],
                "expected_assertions": [],
                "rollback_policy": {"mode": "none"},
                "privacy_class": "private",
                "risk_class": "green",
                "approval": {
                    "operator_approved": False,
                    "approval_id": None,
                    "second_stage_approved": False,
                },
                "idempotency_key": "http-test-v1",
                "evidence_policy": {"public": "aggregate_only"},
            }
        ),
        encoding="utf-8",
    )
    envelope.chmod(0o600)

    with pytest.raises(RunnerExecutionError) as excinfo:
        execute_governed_envelope_file(
            envelope,
            context=ExecutionContext({}, {}, {}, {}),
            evidence_dir=tmp_path / "evidence",
            idempotency_dir=tmp_path / "idempotency",
        )

    assert excinfo.value.reason_code == "EXECUTOR_NOT_REGISTERED"
