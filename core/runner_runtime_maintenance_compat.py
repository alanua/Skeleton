from __future__ import annotations

from collections.abc import Mapping
from typing import Final

import yaml


RUNNER_CONTROLLER_REPAIR_CODEX_STATE_MOUNT_V1: Final = (
    "runner_controller_repair_codex_state_mount_v1"
)
REGISTERED_RUNTIME_MAINTENANCE_OPERATIONS: Final = frozenset(
    {RUNNER_CONTROLLER_REPAIR_CODEX_STATE_MOUNT_V1}
)


def extract_fenced_runtime_maintenance_operation(
    task_content: str | None,
) -> str | None:
    """Return one explicitly registered fenced runtime-maintenance operation.

    This compatibility helper is intentionally fail-closed. It does not weaken the
    legacy exact ``Mode: RUNTIME_MAINTENANCE_TASK`` parser and does not accept an
    arbitrary operation from issue prose.
    """
    if not isinstance(task_content, str) or not task_content.strip():
        return None
    try:
        raw = yaml.safe_load(task_content)
    except yaml.YAMLError:
        return None
    if not isinstance(raw, Mapping):
        return None
    if raw.get("schema") != "skeleton.runner_task.v1":
        return None
    if raw.get("task_kind") != "runtime_maintenance":
        return None
    payload = raw.get("payload")
    if not isinstance(payload, Mapping):
        return None
    operation = payload.get("operation")
    if not isinstance(operation, str):
        return None
    normalized = operation.strip()
    if normalized not in REGISTERED_RUNTIME_MAINTENANCE_OPERATIONS:
        return None
    return normalized
