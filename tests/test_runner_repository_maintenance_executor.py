from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

import core.runner_repository_maintenance_executor as maintenance
from core.runner_repository_maintenance_executor import (
    RegisteredMaintenanceActionError,
    RegisteredMaintenanceExecutor,
    registered_maintenance_task_id,
)
from scripts import runner_poll_github_tasks as runner


def test_registered_executor_maps_only_fixed_actions() -> None:
    calls: list[tuple[str, str, str]] = []
    executor = RegisteredMaintenanceExecutor(
        lambda task_id, workdir, body: calls.append((task_id, workdir, body)) or "DONE: ok",
        "/repo",
    )

    report = executor.run("registered_checkout_recover", "body")

    assert report == "DONE: ok"
    assert calls == [("recover_skeleton_checkout", "/repo", "body")]


def test_unregistered_action_fails_closed() -> None:
    with pytest.raises(RegisteredMaintenanceActionError):
        registered_maintenance_task_id("shell:rm")


def test_codegen_canary_falls_back_for_exact_model_metadata_decoder_failure(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_which(name: str, *, path: str | None = None) -> str | None:
        return {"codex": "/trusted/codex", "openhands": "/trusted/openhands"}.get(name)

    def fake_run(argv: list[str], *, timeout: int = 60, cwd: str | None = None):
        calls.append(argv)
        if argv[:3] == ["git", "init", "-q"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == "/trusted/codex":
            return subprocess.CompletedProcess(
                argv,
                1,
                "",
                "failed to decode models response: unknown variant `max`",
            )
        if argv[0] == "/trusted/openhands":
            return subprocess.CompletedProcess(argv, 0, "RESULT: OK\n", "")
        raise AssertionError(argv)

    monkeypatch.setattr(maintenance.shutil, "which", fake_which)
    monkeypatch.setattr(maintenance, "_run_fixed", fake_run)

    report = maintenance._codegen_read_only_canary()

    assert "reason=OPENHANDS_FALLBACK_CANARY_OK" in report
    assert any(argv[0] == "/trusted/openhands" for argv in calls)


def test_codegen_canary_does_not_fallback_for_unrelated_codex_failure(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_which(name: str, *, path: str | None = None) -> str | None:
        return {"codex": "/trusted/codex", "openhands": "/trusted/openhands"}.get(name)

    def fake_run(argv: list[str], *, timeout: int = 60, cwd: str | None = None):
        calls.append(argv)
        if argv[:3] == ["git", "init", "-q"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == "/trusted/codex":
            return subprocess.CompletedProcess(argv, 9, "", "unrelated synthetic codex failure")
        raise AssertionError("fallback must not run")

    monkeypatch.setattr(maintenance.shutil, "which", fake_which)
    monkeypatch.setattr(maintenance, "_run_fixed", fake_run)

    report = maintenance._codegen_read_only_canary()

    assert "reason=CODEX_CANARY_FAILED" in report
    assert not any(argv[0] == "/trusted/openhands" for argv in calls)


def test_control_plane_recovery_wires_fixed_actions_without_hermes_substitution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    class FakeRegisteredMaintenanceExecutor:
        def __init__(self, dispatch, workdir: str) -> None:
            self.dispatch = dispatch
            self.workdir = workdir

        def run(self, action_id: str, body: str = "") -> str:
            calls.append(action_id)
            return (
                "DONE: Runner host maintenance task completed.\n"
                f"maintenance_task_id={action_id}\n"
                "success_criteria=met"
            )

    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "RegisteredMaintenanceExecutor", FakeRegisteredMaintenanceExecutor)

    body = "\n".join(
        (
            f"Mode: {runner.RUNTIME_MAINTENANCE_MODE}",
            f"Maintenance Task ID: {runner.CONTROL_PLANE_SELF_HEALING_RECOVERY}",
            "Failure Class: CODEGEN_RUNTIME_UNHEALTHY",
            "Failure Key: control:codex-lane",
        )
    )

    report = runner.control_plane_self_healing_recovery(body, str(tmp_path))

    assert report.startswith("DONE:")
    assert "status=RECOVERED" in report
    assert "telegram_notifications=0" in report
    assert calls == [
        "executor_service_preflight",
        "codegen_read_only_canary",
        "queue_reactivate",
    ]
    assert runner.HERMES_WORKER_PREFLIGHT not in calls
