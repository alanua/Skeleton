from __future__ import annotations

from typing import Any

from core.runner_executors import ExecutionContext
from core.secure_broker_factory import secure_default_broker
from core.task_envelope import TaskEnvelope
from core.task_risk_policy import enforce_task_risk
from core.task_rollback import combine_execution_evidence, execute_rollback


def execute_governed_task(
    envelope: TaskEnvelope,
    *,
    context: ExecutionContext,
) -> dict[str, Any]:
    enforce_task_risk(envelope)
    broker = secure_default_broker(context)
    result = broker.execute(envelope)
    if result.get("status") == "DONE":
        return result
    rollback_result = execute_rollback(broker, envelope)
    return combine_execution_evidence(result, rollback_result)
