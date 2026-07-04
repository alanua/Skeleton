from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from core.task_envelope import TaskEnvelope


class RunnerExecutionError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class StepResult:
    executor_class: str
    status: str
    exit_code: int | None = None
    status_code: int | None = None
    output: Any = None


class Executor(Protocol):
    def __call__(self, envelope: TaskEnvelope, step: Mapping[str, Any]) -> StepResult:
        ...


class RunnerExecutionBroker:
    """Dispatch validated TaskEnvelopes through generic executor classes."""

    def __init__(self, executors: Mapping[str, Executor]) -> None:
        self._executors = dict(executors)

    def execute(self, envelope: TaskEnvelope) -> dict[str, Any]:
        executor = self._executors.get(envelope.executor_class)
        if executor is None:
            raise RunnerExecutionError("EXECUTOR_NOT_REGISTERED", envelope.executor_class)

        steps = envelope.steps or ({},)
        results: list[StepResult] = []
        for step in steps:
            result = executor(envelope, step)
            results.append(result)
            if result.status != "DONE":
                break

        assertions = _evaluate_assertions(envelope.expected_assertions, results)
        status = "DONE" if results and all(item.status == "DONE" for item in results) and all(item["passed"] for item in assertions) else "BLOCKED"
        return {
            "schema": "skeleton.runner.execution_result.v1",
            "task_id": envelope.task_id,
            "envelope_hash": envelope.canonical_hash,
            "executor_class": envelope.executor_class,
            "risk_class": envelope.risk_class,
            "privacy_class": envelope.privacy_class,
            "status": status,
            "step_results": [item.__dict__ for item in results],
            "assertions": assertions,
        }


def local_process_executor(envelope: TaskEnvelope, step: Mapping[str, Any]) -> StepResult:
    argv = step.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise RunnerExecutionError("INVALID_ARGV", "local.process requires a non-empty argv array")
    if envelope.shell:
        raise RunnerExecutionError("SHELL_NOT_SUPPORTED_BY_ARGV_EXECUTOR", "use an explicit shell executor policy")

    cwd = step.get("cwd", envelope.cwd)
    if cwd is not None and not isinstance(cwd, str):
        raise RunnerExecutionError("INVALID_CWD", "cwd must be a string")
    stdin_value = step.get("stdin")
    if stdin_value is not None and not isinstance(stdin_value, str):
        stdin_value = json.dumps(stdin_value, sort_keys=True)

    completed = subprocess.run(
        argv,
        cwd=cwd,
        input=stdin_value,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=envelope.timeout_seconds,
        check=False,
        env=_resolved_environment(envelope.environment_refs),
    )
    return StepResult(
        executor_class="local.process",
        status="DONE" if completed.returncode == 0 else "BLOCKED",
        exit_code=completed.returncode,
        output=completed.stdout,
    )


def network_http_executor(envelope: TaskEnvelope, step: Mapping[str, Any]) -> StepResult:
    method = str(step.get("method", "GET")).upper()
    url = step.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise RunnerExecutionError("INVALID_URL", "network.http requires an http or https URL")
    headers = step.get("headers", {})
    if not isinstance(headers, Mapping) or not all(isinstance(key, str) and isinstance(value, str) for key, value in headers.items()):
        raise RunnerExecutionError("INVALID_HEADERS", "headers must be string pairs")
    body = step.get("body")
    data = None
    if body is not None:
        data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body, sort_keys=True).encode("utf-8")

    request = urllib.request.Request(url, data=data, method=method, headers=dict(headers))
    with urllib.request.urlopen(request, timeout=envelope.timeout_seconds) as response:
        raw = response.read()
        content_type = response.headers.get("Content-Type", "")
        output: Any = raw.decode("utf-8", errors="replace")
        if "application/json" in content_type:
            output = json.loads(output)
        return StepResult(
            executor_class="network.http",
            status="DONE" if 200 <= response.status < 300 else "BLOCKED",
            status_code=response.status,
            output=output,
        )


def composite_executor_factory(broker: RunnerExecutionBroker) -> Executor:
    def execute(envelope: TaskEnvelope, step: Mapping[str, Any]) -> StepResult:
        executor_class = step.get("executor_class")
        if not isinstance(executor_class, str):
            raise RunnerExecutionError("INVALID_COMPOSITE_STEP", "composite step requires executor_class")
        executor = broker._executors.get(executor_class)
        if executor is None or executor_class == "composite":
            raise RunnerExecutionError("EXECUTOR_NOT_REGISTERED", executor_class)
        nested = dict(step)
        nested.pop("executor_class", None)
        return executor(envelope, nested)

    return execute


def default_broker() -> RunnerExecutionBroker:
    broker = RunnerExecutionBroker({
        "local.process": local_process_executor,
        "network.http": network_http_executor,
    })
    broker._executors["composite"] = composite_executor_factory(broker)
    return broker


def _resolved_environment(refs: tuple[str, ...]) -> dict[str, str]:
    environment = dict(os.environ)
    for name in refs:
        if name not in os.environ:
            raise RunnerExecutionError("ENVIRONMENT_REFERENCE_MISSING", name)
    return environment


def _evaluate_assertions(assertions: tuple[Mapping[str, Any], ...], results: list[StepResult]) -> list[dict[str, Any]]:
    evaluated: list[dict[str, Any]] = []
    for assertion in assertions:
        kind = assertion.get("kind")
        index = assertion.get("step", len(results) - 1)
        if not isinstance(index, int) or index < 0 or index >= len(results):
            evaluated.append({"kind": kind, "passed": False, "reason": "step_out_of_range"})
            continue
        result = results[index]
        if kind == "exit_code_eq":
            passed = result.exit_code == assertion.get("value")
        elif kind == "status_code_eq":
            passed = result.status_code == assertion.get("value")
        elif kind == "output_contains":
            passed = str(assertion.get("value", "")) in str(result.output)
        elif kind == "json_path_eq":
            passed = _json_path(result.output, assertion.get("path")) == assertion.get("value")
        else:
            passed = False
        evaluated.append({"kind": kind, "passed": passed})
    return evaluated


def _json_path(value: Any, path: object) -> Any:
    if not isinstance(path, str) or not path:
        return None
    current = value
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return None
    return current
