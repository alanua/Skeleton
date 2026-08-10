from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import shutil
import subprocess


REGISTERED_ACTION_CHECK_SKELETON_FRESHNESS = "check_skeleton_freshness"
REGISTERED_ACTION_RECOVER_SKELETON_CHECKOUT = "recover_skeleton_checkout"
REGISTERED_ACTION_REPLENISH_RUNNER_QUEUE = "replenish_runner_queue"

RUNNER_TIMER_UNIT = "skeleton-runner-poll.timer"
RUNNER_SERVICE_UNIT = "skeleton-runner-poll.service"

REGISTERED_REPOSITORY_MAINTENANCE_ACTIONS: Mapping[str, str] = {
    "registered_checkout_recover": REGISTERED_ACTION_RECOVER_SKELETON_CHECKOUT,
    "registered_checkout_freshness_canary": REGISTERED_ACTION_CHECK_SKELETON_FRESHNESS,
    "queue_reactivate": REGISTERED_ACTION_REPLENISH_RUNNER_QUEUE,
}


class RegisteredMaintenanceActionError(ValueError):
    pass


@dataclass(frozen=True)
class RegisteredMaintenanceExecutor:
    dispatch: Callable[[str, str, str], str]
    workdir: str

    def run(self, action_id: str, body: str = "") -> str:
        if action_id == "long_lived_poller_reload":
            return _runner_poller_reload()
        if action_id == "executor_service_recover":
            return _runner_executor_service_recover()
        if action_id == "codegen_runtime_recover":
            return _codegen_runtime_recover()
        if action_id == "codegen_read_only_canary":
            return _codegen_readiness_canary()
        task_id = REGISTERED_REPOSITORY_MAINTENANCE_ACTIONS.get(action_id)
        if task_id is None:
            raise RegisteredMaintenanceActionError("REGISTERED_ACTION_NOT_ALLOWLISTED")
        return self.dispatch(task_id, self.workdir, body)


def registered_maintenance_task_id(action_id: str) -> str:
    fixed = {
        "long_lived_poller_reload": "systemd_user_runner_poller_reload",
        "executor_service_recover": "systemd_user_runner_executor_recover",
        "codegen_runtime_recover": "provider_neutral_codegen_runtime_recover",
        "codegen_read_only_canary": "provider_neutral_codegen_readiness_canary",
    }
    if action_id in fixed:
        return fixed[action_id]
    task_id = REGISTERED_REPOSITORY_MAINTENANCE_ACTIONS.get(action_id)
    if task_id is None:
        raise RegisteredMaintenanceActionError("REGISTERED_ACTION_NOT_ALLOWLISTED")
    return task_id


def _done(*lines: str) -> str:
    return "\n".join(
        (
            "DONE: Runner host maintenance task completed.",
            *lines,
            "success_criteria=met",
        )
    )


def _blocked(reason: str) -> str:
    return "\n".join(
        (
            "BLOCKED: Runner host maintenance task not completed.",
            f"reason={reason}",
            "success_criteria=not_met",
        )
    )


def _run_fixed(argv: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _runner_poller_reload() -> str:
    try:
        restart = _run_fixed(["systemctl", "--user", "restart", RUNNER_TIMER_UNIT])
        if restart.returncode != 0:
            return _blocked("RUNNER_POLLER_TIMER_RESTART_FAILED")
        active = _run_fixed(["systemctl", "--user", "is-active", RUNNER_TIMER_UNIT])
        if active.returncode != 0 or active.stdout.strip() != "active":
            return _blocked("RUNNER_POLLER_TIMER_NOT_ACTIVE")
        return _done(
            "component=runner_scheduler_poller",
            "recovery=timer_restart",
            "timer_active=true",
        )
    except (OSError, subprocess.SubprocessError):
        return _blocked("RUNNER_POLLER_RECOVERY_EXECUTION_FAILED")


def _runner_executor_service_recover() -> str:
    try:
        timer = _run_fixed(["systemctl", "--user", "start", RUNNER_TIMER_UNIT])
        if timer.returncode != 0:
            return _blocked("RUNNER_EXECUTOR_TIMER_START_FAILED")
        active = _run_fixed(["systemctl", "--user", "is-active", RUNNER_TIMER_UNIT])
        if active.returncode != 0 or active.stdout.strip() != "active":
            return _blocked("RUNNER_EXECUTOR_TIMER_NOT_ACTIVE")
        return _done(
            "component=runner_executor",
            "recovery=timer_start",
            "timer_active=true",
        )
    except (OSError, subprocess.SubprocessError):
        return _blocked("RUNNER_EXECUTOR_RECOVERY_EXECUTION_FAILED")


def _codegen_runtime_recover() -> str:
    codex_present = shutil.which("codex") is not None
    openhands_present = shutil.which("openhands") is not None
    if not codex_present and not openhands_present:
        return _blocked("NO_APPROVED_CODEGEN_EXECUTOR_AVAILABLE")
    if openhands_present:
        return _done(
            "component=codegen_runtime",
            "fallback_executor=openhands",
            f"codex_present={'true' if codex_present else 'false'}",
            "openhands_present=true",
        )
    return _done(
        "component=codegen_runtime",
        "fallback_executor=codex",
        "codex_present=true",
        "openhands_present=false",
    )


def _codegen_readiness_canary() -> str:
    candidates = (
        ("openhands", ["openhands", "--version"]),
        ("codex", ["codex", "--version"]),
    )
    for name, argv in candidates:
        if shutil.which(argv[0]) is None:
            continue
        try:
            result = _run_fixed(argv, timeout=20)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            return _done(
                "component=codegen_runtime",
                f"canary_executor={name}",
                "read_only_canary=true",
            )
    return _blocked("CODEGEN_READ_ONLY_CANARY_FAILED")
