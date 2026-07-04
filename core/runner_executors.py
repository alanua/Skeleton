from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from core.runner_execution_broker import RunnerExecutionError, StepResult
from core.task_envelope import TaskEnvelope


@dataclass(frozen=True)
class ExecutionContext:
    targets: Mapping[str, Mapping[str, str]]
    entrypoints: Mapping[str, Callable[[Any], Any]]
    roots: Mapping[str, Path]
    environment: Mapping[str, str]


GENERIC_EXECUTOR_CLASSES = frozenset(
    {
        "local.process",
        "remote.ssh",
        "network.http",
        "python.entrypoint",
        "filesystem",
        "repository",
        "composite",
    }
)


def python_entrypoint_executor(context: ExecutionContext):
    def execute(envelope: TaskEnvelope, step: Mapping[str, Any]) -> StepResult:
        name = step.get("entrypoint")
        if not isinstance(name, str) or name not in context.entrypoints:
            raise RunnerExecutionError("ENTRYPOINT_NOT_REGISTERED", str(name))
        output = context.entrypoints[name](step.get("input", envelope.input))
        return StepResult(
            executor_class="python.entrypoint",
            status="DONE",
            output=output,
        )

    return execute


def filesystem_executor(context: ExecutionContext):
    def execute(_envelope: TaskEnvelope, step: Mapping[str, Any]) -> StepResult:
        root_name = step.get("root")
        if not isinstance(root_name, str) or root_name not in context.roots:
            raise RunnerExecutionError("ROOT_NOT_REGISTERED", str(root_name))
        root = context.roots[root_name].expanduser().resolve(strict=False)
        operation = step.get("operation")
        path = _safe_path(root, step.get("path"))

        if operation == "read_text":
            output: Any = path.read_text(encoding="utf-8")
        elif operation == "write_text":
            value = step.get("value")
            if not isinstance(value, str):
                raise RunnerExecutionError(
                    "INVALID_TEXT_VALUE",
                    "write_text requires string value",
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
            output = {"written": True, "bytes": len(value.encode("utf-8"))}
        elif operation == "write_json":
            path.parent.mkdir(parents=True, exist_ok=True)
            rendered = json.dumps(
                step.get("value"),
                sort_keys=True,
                separators=(",", ":"),
            ) + "\n"
            path.write_text(rendered, encoding="utf-8")
            output = {"written": True, "bytes": len(rendered.encode("utf-8"))}
        elif operation == "mkdir":
            path.mkdir(parents=True, exist_ok=True)
            output = {"created": True}
        elif operation in {"copy", "move"}:
            destination = _safe_path(root, step.get("destination"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            if operation == "copy":
                shutil.copy2(path, destination)
            else:
                shutil.move(str(path), str(destination))
            output = {"completed": True}
        else:
            raise RunnerExecutionError(
                "FILESYSTEM_OPERATION_NOT_SUPPORTED",
                str(operation),
            )

        return StepResult(
            executor_class="filesystem",
            status="DONE",
            output=output,
        )

    return execute


def _safe_path(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative or "\x00" in relative:
        raise RunnerExecutionError(
            "INVALID_RELATIVE_PATH",
            "path must be a non-empty string",
        )
    candidate = (root / relative).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RunnerExecutionError(
            "PATH_OUTSIDE_REGISTERED_ROOT",
            relative,
        ) from exc
    return candidate
