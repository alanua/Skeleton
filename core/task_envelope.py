from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

TASK_ENVELOPE_SCHEMA = "skeleton.runner.task_envelope.v1"
EXECUTOR_CLASSES = frozenset({
    "local.process",
    "remote.ssh",
    "network.http",
    "python.entrypoint",
    "filesystem",
    "repository",
    "composite",
})
RISK_CLASSES = frozenset({"green", "yellow", "red"})
PRIVACY_CLASSES = frozenset({"public", "private", "secret"})
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class TaskEnvelopeError(ValueError):
    pass


@dataclass(frozen=True)
class Approval:
    operator_approved: bool
    approval_id: str | None
    second_stage_approved: bool


@dataclass(frozen=True)
class TaskEnvelope:
    task_id: str
    executor_class: str
    target: str | None
    steps: tuple[Mapping[str, Any], ...]
    cwd: str | None
    timeout_seconds: int
    input: Any
    environment_refs: tuple[str, ...]
    expected_assertions: tuple[Mapping[str, Any], ...]
    rollback_policy: Mapping[str, Any]
    privacy_class: str
    risk_class: str
    approval: Approval
    idempotency_key: str
    evidence_policy: Mapping[str, Any]
    shell: bool = False

    @property
    def canonical_hash(self) -> str:
        payload = canonical_envelope_payload(self)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def parse_task_envelope(data: Mapping[str, Any]) -> TaskEnvelope:
    if not isinstance(data, Mapping):
        raise TaskEnvelopeError("task envelope must be an object")
    if data.get("schema") != TASK_ENVELOPE_SCHEMA:
        raise TaskEnvelopeError("unsupported task envelope schema")

    task_id = _token(data.get("task_id"), "task_id")
    executor_class = _token(data.get("executor_class"), "executor_class")
    if executor_class not in EXECUTOR_CLASSES:
        raise TaskEnvelopeError("executor_class is not registered")

    target_value = data.get("target")
    target = None if target_value is None else _token(target_value, "target")

    steps_value = data.get("steps", [])
    if not isinstance(steps_value, list) or len(steps_value) > 64:
        raise TaskEnvelopeError("steps must be an array with at most 64 items")
    steps = tuple(_mapping(item, "step") for item in steps_value)
    if executor_class == "composite" and not steps:
        raise TaskEnvelopeError("composite tasks require steps")

    cwd_value = data.get("cwd")
    cwd = None if cwd_value is None else _bounded_string(cwd_value, "cwd", 1024)

    timeout_seconds = data.get("timeout_seconds", 90)
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 86400:
        raise TaskEnvelopeError("timeout_seconds must be between 1 and 86400")

    refs_value = data.get("environment_refs", [])
    if not isinstance(refs_value, list) or len(refs_value) > 64:
        raise TaskEnvelopeError("environment_refs must be a bounded array")
    environment_refs = tuple(_token(item, "environment_ref") for item in refs_value)

    assertions_value = data.get("expected_assertions", [])
    if not isinstance(assertions_value, list) or len(assertions_value) > 64:
        raise TaskEnvelopeError("expected_assertions must be a bounded array")
    expected_assertions = tuple(_mapping(item, "expected_assertion") for item in assertions_value)

    privacy_class = _token(data.get("privacy_class"), "privacy_class")
    if privacy_class not in PRIVACY_CLASSES:
        raise TaskEnvelopeError("unsupported privacy_class")
    risk_class = _token(data.get("risk_class"), "risk_class")
    if risk_class not in RISK_CLASSES:
        raise TaskEnvelopeError("unsupported risk_class")

    approval_data = _mapping(data.get("approval", {}), "approval")
    operator_approved = approval_data.get("operator_approved") is True
    approval_id_value = approval_data.get("approval_id")
    approval_id = None if approval_id_value is None else _token(approval_id_value, "approval_id")
    second_stage_approved = approval_data.get("second_stage_approved") is True
    approval = Approval(operator_approved, approval_id, second_stage_approved)

    if risk_class in {"yellow", "red"} and not operator_approved:
        raise TaskEnvelopeError("operator approval is required")
    if risk_class == "red" and not second_stage_approved:
        raise TaskEnvelopeError("second-stage approval is required")

    shell = data.get("shell", False)
    if not isinstance(shell, bool):
        raise TaskEnvelopeError("shell must be boolean")
    if shell and risk_class == "green":
        raise TaskEnvelopeError("shell mode is not allowed for green tasks")

    idempotency_key = _token(data.get("idempotency_key"), "idempotency_key")

    return TaskEnvelope(
        task_id=task_id,
        executor_class=executor_class,
        target=target,
        steps=steps,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        input=data.get("input"),
        environment_refs=environment_refs,
        expected_assertions=expected_assertions,
        rollback_policy=_mapping(data.get("rollback_policy", {}), "rollback_policy"),
        privacy_class=privacy_class,
        risk_class=risk_class,
        approval=approval,
        idempotency_key=idempotency_key,
        evidence_policy=_mapping(data.get("evidence_policy", {}), "evidence_policy"),
        shell=shell,
    )


def canonical_envelope_payload(envelope: TaskEnvelope) -> dict[str, Any]:
    return {
        "schema": TASK_ENVELOPE_SCHEMA,
        "task_id": envelope.task_id,
        "executor_class": envelope.executor_class,
        "target": envelope.target,
        "steps": [dict(step) for step in envelope.steps],
        "cwd": envelope.cwd,
        "timeout_seconds": envelope.timeout_seconds,
        "input": envelope.input,
        "environment_refs": list(envelope.environment_refs),
        "expected_assertions": [dict(item) for item in envelope.expected_assertions],
        "rollback_policy": dict(envelope.rollback_policy),
        "privacy_class": envelope.privacy_class,
        "risk_class": envelope.risk_class,
        "approval": {
            "operator_approved": envelope.approval.operator_approved,
            "approval_id": envelope.approval.approval_id,
            "second_stage_approved": envelope.approval.second_stage_approved,
        },
        "idempotency_key": envelope.idempotency_key,
        "evidence_policy": dict(envelope.evidence_policy),
        "shell": envelope.shell,
    }


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TaskEnvelopeError(f"{name} must be an object")
    return value


def _token(value: object, name: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise TaskEnvelopeError(f"{name} must be a safe token")
    return value


def _bounded_string(value: object, name: str, max_length: int) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length or "\x00" in value:
        raise TaskEnvelopeError(f"{name} must be a bounded string")
    return value
