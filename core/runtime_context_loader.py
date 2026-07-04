from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.runner_executors import ExecutionContext


class RuntimeContextError(ValueError):
    pass


def read_registry(path_value: str | Path) -> dict[str, Any]:
    path = Path(path_value).expanduser().resolve(strict=True)
    if not path.is_file():
        raise RuntimeContextError("registry path is not a file")
    if path.stat().st_mode & 0o077:
        raise RuntimeContextError("registry file must be owner-only")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeContextError("registry JSON must be an object")
    return value
