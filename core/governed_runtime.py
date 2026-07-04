from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from core.runner_executors import ExecutionContext
from core.secure_task_execution import execute_governed_task
from core.task_envelope import parse_task_envelope


class GovernedRuntimeError(RuntimeError):
    pass


def execute_governed_envelope_file(
    envelope_path: str | Path,
    *,
    context: ExecutionContext,
) -> dict[str, Any]:
    path = Path(envelope_path).expanduser().resolve(strict=True)
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise GovernedRuntimeError("envelope file must be private")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise GovernedRuntimeError("envelope JSON must be an object")
    envelope = parse_task_envelope(value)
    return execute_governed_task(envelope, context=context)
