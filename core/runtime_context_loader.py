from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

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


def load_runtime_context(
    *,
    target_registry_path: str | Path | None = None,
    root_registry_path: str | Path | None = None,
) -> ExecutionContext:
    target_values = (
        read_registry(target_registry_path)
        if target_registry_path is not None
        else {}
    )
    root_values = (
        read_registry(root_registry_path)
        if root_registry_path is not None
        else {}
    )

    targets: dict[str, Mapping[str, str]] = {}
    for name, raw_target in target_values.items():
        if not isinstance(name, str) or not isinstance(raw_target, Mapping):
            raise RuntimeContextError("target registry is malformed")
        target: dict[str, str] = {}
        for key, value in raw_target.items():
            if not isinstance(key, str) or not isinstance(value, str) or not value:
                raise RuntimeContextError("target values must be non-empty strings")
            target[key] = value
        targets[name] = target

    roots: dict[str, Path] = {}
    for name, raw_path in root_values.items():
        if not isinstance(name, str) or not isinstance(raw_path, str):
            raise RuntimeContextError("root registry is malformed")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            raise RuntimeContextError("registered roots must be absolute")
        roots[name] = path.resolve(strict=False)

    return ExecutionContext(
        targets=targets,
        entrypoints={},
        roots=roots,
        environment={},
    )
