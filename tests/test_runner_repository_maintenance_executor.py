from __future__ import annotations

import subprocess

import pytest

import core.runner_repository_maintenance_executor as maintenance
from core.runner_repository_maintenance_executor import (
    RegisteredMaintenanceActionError,
    RegisteredMaintenanceExecutor,
    registered_maintenance_task_id,
)


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
