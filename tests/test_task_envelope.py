from __future__ import annotations

import pytest

from core.task_envelope import TaskEnvelopeError, parse_task_envelope


def envelope(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": "skeleton.runner.task_envelope.v1",
        "task_id": "task-001",
        "executor_class": "network.http",
        "target": "registered-device",
        "steps": [{"method": "GET", "url": "http://device.local/status"}],
        "timeout_seconds": 30,
        "environment_refs": [],
        "expected_assertions": [{"kind": "status_code_eq", "value": 200}],
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
    value.update(overrides)
    return value


def test_parse_operator_approved_envelope() -> None:
    parsed = parse_task_envelope(envelope())

    assert parsed.task_id == "task-001"
    assert parsed.executor_class == "network.http"
    assert parsed.risk_class == "yellow"
    assert parsed.approval.operator_approved is True
    assert len(parsed.canonical_hash) == 64


def test_yellow_requires_operator_approval() -> None:
    candidate = envelope(
        approval={
            "operator_approved": False,
            "approval_id": None,
            "second_stage_approved": False,
        }
    )

    with pytest.raises(TaskEnvelopeError, match="operator approval"):
        parse_task_envelope(candidate)


def test_red_requires_second_stage_approval() -> None:
    candidate = envelope(risk_class="red")

    with pytest.raises(TaskEnvelopeError, match="second-stage"):
        parse_task_envelope(candidate)


def test_unknown_executor_is_rejected() -> None:
    with pytest.raises(TaskEnvelopeError, match="not registered"):
        parse_task_envelope(envelope(executor_class="device.wled"))


def test_composite_requires_steps() -> None:
    with pytest.raises(TaskEnvelopeError, match="require steps"):
        parse_task_envelope(envelope(executor_class="composite", steps=[]))
