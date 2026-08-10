from __future__ import annotations

import pytest

from core.runner_repository_maintenance_executor import (
    RegisteredMaintenanceActionError,
    RegisteredMaintenanceExecutor,
    registered_maintenance_task_id,
)


def test_registered_executor_maps_repository_actions() -> None:
    calls: list[tuple[str, str, str]] = []
    executor = RegisteredMaintenanceExecutor(
        lambda task_id, workdir, body: calls.append((task_id, workdir, body)) or "DONE: ok",
        "/repo",
    )
    report = executor.run("registered_checkout_recover", "body")
    assert report == "DONE: ok"
    assert calls == [("recover_skeleton_checkout", "/repo", "body")]


def test_fixed_runtime_actions_are_not_telegram_or_hermes_aliases() -> None:
    assert registered_maintenance_task_id("long_lived_poller_reload") == "systemd_user_runner_poller_reload"
    assert registered_maintenance_task_id("executor_service_recover") == "systemd_user_runner_executor_recover"
    assert registered_maintenance_task_id("codegen_runtime_recover") == "provider_neutral_codegen_runtime_recover"
    assert registered_maintenance_task_id("codegen_read_only_canary") == "provider_neutral_codegen_readiness_canary"


def test_unregistered_action_fails_closed() -> None:
    with pytest.raises(RegisteredMaintenanceActionError):
        registered_maintenance_task_id("shell:rm")
