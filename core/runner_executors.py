from __future__ import annotations

import json
import shutil
import subprocess
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


def remote_ssh_executor(context: ExecutionContext):
    def execute(envelope: TaskEnvelope, step: Mapping[str, Any]) -> StepResult:
        target_name = step.get("target", envelope.target)
        if not isinstance(target_name, str) or target_name not in context.targets:
            raise RunnerExecutionError("TARGET_NOT_REGISTERED", str(target_name))
        target = context.targets[target_name]
        required = ("host", "user", "identity_file", "known_hosts_file")
        if any(not target.get(key) for key in required):
            raise RunnerExecutionError(
                "TARGET_CONFIGURATION_INCOMPLETE",
                target_name,
            )

        argv = step.get("argv")
        if not isinstance(argv, list) or not argv:
            raise RunnerExecutionError("INVALID_ARGV", "remote.ssh requires argv")
        if not all(isinstance(item, str) and item for item in argv):
            raise RunnerExecutionError("INVALID_ARGV", "remote argv must contain strings")

        stdin_value = step.get("stdin")
        if stdin_value is not None and not isinstance(stdin_value, str):
            stdin_value = json.dumps(stdin_value, sort_keys=True)

        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={target['known_hosts_file']}",
            "-i",
            target["identity_file"],
            f"{target['user']}@{target['host']}",
            *argv,
        ]
        completed = subprocess.run(
            command,
            input=stdin_value,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=envelope.timeout_seconds,
            check=False,
            env=dict(context.environment),
        )
        return StepResult(
            executor_class="remote.ssh",
            status="DONE" if completed.returncode == 0 else "BLOCKED",
            exit_code=completed.returncode,
            output=completed.stdout,
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


def repository_executor(context: ExecutionContext):
    def execute(envelope: TaskEnvelope, step: Mapping[str, Any]) -> StepResult:
        root_name = step.get("root")
        if not isinstance(root_name, str) or root_name not in context.roots:
            raise RunnerExecutionError("ROOT_NOT_REGISTERED", str(root_name))
        root = context.roots[root_name].expanduser().resolve(strict=False)
        argv = step.get("argv")
        if not isinstance(argv, list) or not argv or argv[0] != "git":
            raise RunnerExecutionError(
                "INVALID_REPOSITORY_ARGV",
                "repository executor requires git argv",
            )
        if not all(isinstance(item, str) and item for item in argv):
            raise RunnerExecutionError(
                "INVALID_REPOSITORY_ARGV",
                "repository argv must contain strings",
            )
        completed = subprocess.run(
            argv,
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=envelope.timeout_seconds,
            check=False,
            env=dict(context.environment),
        )
        return StepResult(
            executor_class="repository",
            status="DONE" if completed.returncode == 0 else "BLOCKED",
            exit_code=completed.returncode,
            output=completed.stdout,
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
            "path is outside registered root",
        ) from exc
    return candidate
