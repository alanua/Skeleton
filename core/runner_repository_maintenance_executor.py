from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass


REGISTERED_ACTION_CHECK_SKELETON_FRESHNESS = "check_skeleton_freshness"
REGISTERED_ACTION_RECOVER_SKELETON_CHECKOUT = "recover_skeleton_checkout"
REGISTERED_ACTION_SYNC_POLLER_RUNTIME = "sync_telegram_callback_poller_runtime"
REGISTERED_ACTION_HERMES_WORKER_PREFLIGHT = "hermes_worker_preflight"
REGISTERED_ACTION_REPLENISH_RUNNER_QUEUE = "replenish_runner_queue"

REGISTERED_REPOSITORY_MAINTENANCE_ACTIONS: Mapping[str, str] = {
    "registered_checkout_recover": REGISTERED_ACTION_RECOVER_SKELETON_CHECKOUT,
    "registered_checkout_freshness_canary": REGISTERED_ACTION_CHECK_SKELETON_FRESHNESS,
    "long_lived_poller_reload": REGISTERED_ACTION_SYNC_POLLER_RUNTIME,
    "executor_service_preflight": REGISTERED_ACTION_HERMES_WORKER_PREFLIGHT,
    "codegen_read_only_canary": REGISTERED_ACTION_HERMES_WORKER_PREFLIGHT,
    "queue_reactivate": REGISTERED_ACTION_REPLENISH_RUNNER_QUEUE,
}


class RegisteredMaintenanceActionError(ValueError):
    pass


@dataclass(frozen=True)
class RegisteredMaintenanceExecutor:
    dispatch: Callable[[str, str, str], str]
    workdir: str

    def run(self, action_id: str, body: str = "") -> str:
        task_id = REGISTERED_REPOSITORY_MAINTENANCE_ACTIONS.get(action_id)
        if task_id is None:
            raise RegisteredMaintenanceActionError("REGISTERED_ACTION_NOT_ALLOWLISTED")
        return self.dispatch(task_id, self.workdir, body)


def registered_maintenance_task_id(action_id: str) -> str:
    task_id = REGISTERED_REPOSITORY_MAINTENANCE_ACTIONS.get(action_id)
    if task_id is None:
        raise RegisteredMaintenanceActionError("REGISTERED_ACTION_NOT_ALLOWLISTED")
    return task_id
