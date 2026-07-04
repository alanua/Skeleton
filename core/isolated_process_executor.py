from __future__ import annotations

import json
import subprocess
from typing import Any, Mapping

from core.runner_execution_broker import RunnerExecutionError, StepResult
from core.task_envelope import TaskEnvelope

SAFE_PROCESS_ENVIRONMENT = {
    "LANG": "C.UTF-8",
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
}


def isolated_local_process_executor(
    envelope: TaskEnvelope,
    step: Mapping[str, Any],
) -> StepResult:
    if envelope.environment_refs:
        raise RunnerExecutionError(
            "ENVIRONMENT_REFERENCES_DISABLED",
            "named environment references require a dedicated secret resolver",
        )
    argv = step.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(item, str) and item for item in argv)
    ):
        raise RunnerExecutionError("INVALID_ARGV", "argv must contain strings")
    if envelope.shell:
        raise RunnerExecutionError("SHELL_NOT_SUPPORTED", "shell mode is disabled")
    stdin_value = step.get("stdin")
    if stdin_value is not None and not isinstance(stdin_value, str):
        stdin_value = json.dumps(stdin_value, sort_keys=True)
    completed = subprocess.run(
        argv,
        cwd=step.get("cwd", envelope.cwd),
        input=stdin_value,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=envelope.timeout_seconds,
        check=False,
        env=dict(SAFE_PROCESS_ENVIRONMENT),
    )
    return StepResult(
        executor_class="local.process",
        status="DONE" if completed.returncode == 0 else "BLOCKED",
        exit_code=completed.returncode,
        output=completed.stdout,
    )
