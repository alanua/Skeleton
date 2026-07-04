from __future__ import annotations

from typing import Any

from core.runner_executors import ExecutionContext
from core.secure_broker_factory import secure_default_broker
from core.task_envelope import TaskEnvelope
from core.task_risk_policy import enforce_task_risk


def execute_governed_task(
    envelope: TaskEnvelope,
    *,
    context: ExecutionContext,
) -> dict[str, Any]:
    enforce_task_risk(envelope)
    return secure_default_broker(context).execute(envelope)
