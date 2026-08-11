from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from core.codex_runtime_recovery import (
    CodexRuntimeRecoveryError,
    TARGET_CODEX_MODEL,
    TARGET_CODEX_VERSION,
    pinned_codex_runtime_path,
    recover_pinned_codex_runtime,
)


REGISTERED_ACTION_CHECK_SKELETON_FRESHNESS = "check_skeleton_freshness"
REGISTERED_ACTION_RECOVER_SKELETON_CHECKOUT = "recover_skeleton_checkout"
REGISTERED_ACTION_REPLENISH_RUNNER_QUEUE = "replenish_runner_queue"

RUNNER_SERVICE = "skeleton-runner-poll.service"
RUNNER_TIMER = "skeleton-runner-poll.timer"
HOME_EDGE_ENV_PREFIX = "SKELETON_HOME_EDGE_01_"
HOME_EDGE_EXEC_HMAC_SECRET_ENV = "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET"
_FIXED_LOCAL_ACTIONS = frozenset(
    {
        "long_lived_poller_reload",
        "executor_service_preflight",
        "codegen_runtime_recover",
        "codegen_read_only_canary",
    }
)

REGISTERED_REPOSITORY_MAINTENANCE_ACTIONS: Mapping[str, str] = {
    "registered_checkout_recover": REGISTERED_ACTION_RECOVER_SKELETON_CHECKOUT,
    "registered_checkout_freshness_canary": REGISTERED_ACTION_CHECK_SKELETON_FRESHNESS,
    "queue_reactivate": REGISTERED_ACTION_REPLENISH_RUNNER_QUEUE,
}


class RegisteredMaintenanceActionError(ValueError):
    pass


def _report(status: str, action_id: str, reason: str) -> str:
    success = "met" if status == "DONE" else "not_met"
    return (
        f"{status}: Runner host maintenance task completed.\n"
        f"maintenance_task_id={action_id}\n"
        f"reason={reason}\n"
        f"success_criteria={success}"
    )


def _safe_child_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(HOME_EDGE_ENV_PREFIX) and key != HOME_EDGE_EXEC_HMAC_SECRET_ENV
    }


def _run_fixed(argv: list[str], *, timeout: int = 60, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=_safe_child_environment(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _recover_runner_timer() -> str:
    for argv in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "reset-failed", RUNNER_SERVICE],
        ["systemctl", "--user", "start", RUNNER_TIMER],
        ["systemctl", "--user", "is-active", "--quiet", RUNNER_TIMER],
    ):
        result = _run_fixed(argv)
        if result.returncode != 0:
            return _report("BLOCKED", "long_lived_poller_reload", "RUNNER_TIMER_RECOVERY_FAILED")
    return _report("DONE", "long_lived_poller_reload", "RUNNER_TIMER_ACTIVE")


def _recover_executor_service() -> str:
    report = _recover_runner_timer()
    if not report.startswith("DONE:"):
        return _report("BLOCKED", "executor_service_preflight", "RUNNER_EXECUTOR_RECOVERY_FAILED")
    return _report("DONE", "executor_service_preflight", "RUNNER_EXECUTOR_REARMED_BY_TIMER")


def _public_recovery_reason(reason: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "_", reason).strip("_").upper()
    return f"CODEX_RUNTIME_RECOVERY_{token or 'FAILED'}"


def _recover_codegen_runtime() -> str:
    environment = _safe_child_environment()
    try:
        recovery = recover_pinned_codex_runtime(environment)
    except (OSError, subprocess.SubprocessError):
        return _report("BLOCKED", "codegen_runtime_recover", "CODEX_RUNTIME_RECOVERY_EXCEPTION")
    if not recovery.success:
        return _report("BLOCKED", "codegen_runtime_recover", _public_recovery_reason(recovery.reason))

    try:
        codex = pinned_codex_runtime_path(environment)
    except (CodexRuntimeRecoveryError, OSError, subprocess.SubprocessError):
        return _report("BLOCKED", "codegen_runtime_recover", "CODEX_RUNTIME_VERSION_UNVERIFIED")
    version = _run_fixed([codex, "--version"], timeout=15)
    if version.returncode != 0 or version.stdout.strip() != f"codex-cli {TARGET_CODEX_VERSION}":
        return _report("BLOCKED", "codegen_runtime_recover", "CODEX_RUNTIME_VERSION_UNVERIFIED")
    success_reason = (
        "CODEX_RUNTIME_RECOVERED_PROVIDER_UNAVAILABLE"
        if recovery.reason == "ready_provider_unavailable"
        else "CODEX_RUNTIME_RECOVERED"
    )
    return _report("DONE", "codegen_runtime_recover", success_reason)


def _quota_or_provider_outage(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "usage limit",
        "rate limit",
        "quota",
        "insufficient_quota",
        "provider unavailable",
        "temporarily unavailable",
        "service unavailable",
        "try again at",
    )
    return any(marker in lowered for marker in markers)


def _fallback_allowed(text: str) -> bool:
    return _quota_or_provider_outage(text)


def _codegen_read_only_canary() -> str:
    with tempfile.TemporaryDirectory(prefix="skeleton-codegen-canary-") as raw_dir:
        workdir = Path(raw_dir)
        init = _run_fixed(["git", "init", "-q"], cwd=str(workdir))
        if init.returncode != 0:
            return _report("BLOCKED", "codegen_read_only_canary", "CANARY_GIT_INIT_FAILED")

        environment = _safe_child_environment()
        try:
            codex = pinned_codex_runtime_path(environment)
        except (CodexRuntimeRecoveryError, OSError, subprocess.SubprocessError):
            return _report("BLOCKED", "codegen_read_only_canary", "CODEX_CANARY_RUNTIME_UNVERIFIED")

        result = _run_fixed(
            [
                codex,
                "exec",
                "--sandbox",
                "read-only",
                "--model",
                TARGET_CODEX_MODEL,
                "--cd",
                str(workdir),
                "Return exactly RESULT: OK. Do not modify files.",
            ],
            timeout=120,
        )
        combined = f"{result.stdout}\n{result.stderr}"
        if result.returncode == 0 and "RESULT: OK" in combined:
            return _report("DONE", "codegen_read_only_canary", "CODEX_CANARY_OK")
        if not _fallback_allowed(combined):
            return _report("BLOCKED", "codegen_read_only_canary", "CODEX_CANARY_FAILED")

        openhands = shutil.which("openhands", path=environment.get("PATH"))
        if not openhands:
            return _report("BLOCKED", "codegen_read_only_canary", "NO_FALLBACK_PROVIDER")
        result = _run_fixed(
            [
                openhands,
                "--headless",
                "--json",
                "-t",
                "Return exactly RESULT: OK. Do not modify files.",
            ],
            timeout=180,
            cwd=str(workdir),
        )
        combined = f"{result.stdout}\n{result.stderr}"
        if result.returncode == 0 and "RESULT: OK" in combined:
            return _report("DONE", "codegen_read_only_canary", "OPENHANDS_FALLBACK_CANARY_OK")
        return _report("BLOCKED", "codegen_read_only_canary", "OPENHANDS_FALLBACK_CANARY_FAILED")


@dataclass(frozen=True)
class RegisteredMaintenanceExecutor:
    dispatch: Callable[[str, str, str], str]
    workdir: str

    def run(self, action_id: str, body: str = "") -> str:
        if action_id == "long_lived_poller_reload":
            return _recover_runner_timer()
        if action_id == "executor_service_preflight":
            return _recover_executor_service()
        if action_id == "codegen_runtime_recover":
            return _recover_codegen_runtime()
        if action_id == "codegen_read_only_canary":
            return _codegen_read_only_canary()
        task_id = REGISTERED_REPOSITORY_MAINTENANCE_ACTIONS.get(action_id)
        if task_id is None:
            raise RegisteredMaintenanceActionError("REGISTERED_ACTION_NOT_ALLOWLISTED")
        return self.dispatch(task_id, self.workdir, body)


def registered_maintenance_task_id(action_id: str) -> str:
    if action_id in _FIXED_LOCAL_ACTIONS:
        return action_id
    task_id = REGISTERED_REPOSITORY_MAINTENANCE_ACTIONS.get(action_id)
    if task_id is None:
        raise RegisteredMaintenanceActionError("REGISTERED_ACTION_NOT_ALLOWLISTED")
    return task_id
