from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any, Mapping

from core.runner_execution_broker import RunnerExecutionBroker
from core.task_envelope import TaskEnvelope
from core.task_risk_policy import enforce_task_risk


class TaskRollbackError(ValueError):
    pass


def execute_rollback(
    broker: RunnerExecutionBroker,
    envelope: TaskEnvelope,
) -> dict[str, Any] | None:
    policy = envelope.rollback_policy
    if policy.get("mode") != "steps":
        return None
    raw_steps = policy.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise TaskRollbackError("rollback steps must be a non-empty array")
    steps: list[Mapping[str, Any]] = []
    for item in raw_steps:
        if not isinstance(item, Mapping):
            raise TaskRollbackError("rollback step must be an object")
        steps.append(item)

    rollback_envelope = replace(
        envelope,
        task_id=f"{envelope.task_id}:rollback",
        executor_class="composite",
        steps=tuple(steps),
        expected_assertions=(),
        rollback_policy={
            "mode": "irreversible",
            "reason": "rollback phase does not recurse",
        },
        idempotency_key=f"{envelope.idempotency_key}:rollback",
    )
    enforce_task_risk(rollback_envelope)
    return broker.execute(rollback_envelope)


def combine_execution_evidence(
    result: Mapping[str, Any],
    rollback_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if rollback_result is None:
        return dict(result)
    evidence = {
        "schema": "skeleton.runner.governed_execution_evidence.v1",
        "execution": result["private_evidence"],
        "rollback": rollback_result["private_evidence"],
    }
    evidence_hash = hashlib.sha256(
        json.dumps(
            evidence,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    receipt = dict(result["public_receipt"])
    receipt["evidence_hash"] = evidence_hash
    receipt["rollback_status"] = rollback_result.get("status")
    receipt["rollback_step_count"] = len(
        rollback_result.get("step_results", [])
    )
    combined = dict(result)
    combined["private_evidence"] = evidence
    combined["public_receipt"] = receipt
    return combined
