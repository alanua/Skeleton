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


def test_codegen_runtime_recovery_pins_and_verifies_exact_version(monkeypatch) -> None:
    calls: list[str] = []

    def fake_recover(environment: dict[str, str]):
        assert maintenance.HOME_EDGE_EXEC_HMAC_SECRET_ENV not in environment
        calls.append("recover")
        return type("Result", (), {"success": True, "reason": "ready"})()

    monkeypatch.setattr(maintenance, "recover_pinned_codex_runtime", fake_recover)
    monkeypatch.setattr(maintenance, "pinned_codex_runtime_path", lambda _environment: "/canonical/npm/bin/codex")
    monkeypatch.setattr(
        maintenance,
        "_run_fixed",
        lambda argv, *, timeout=60, cwd=None: subprocess.CompletedProcess(
            argv, 0, f"codex-cli {maintenance.TARGET_CODEX_VERSION}\n", ""
        ),
    )
    report = maintenance._recover_codegen_runtime()
    assert report.startswith("DONE:")
    assert "reason=CODEX_RUNTIME_RECOVERED" in report
    assert calls == ["recover"]


def test_codegen_runtime_recovery_surfaces_stable_phase_reason(monkeypatch) -> None:
    monkeypatch.setattr(
        maintenance,
        "recover_pinned_codex_runtime",
        lambda _environment: type(
            "Result", (), {"success": False, "reason": "npm_runtime_binary_missing"}
        )(),
    )
    report = maintenance._recover_codegen_runtime()
    assert report.startswith("BLOCKED:")
    assert "reason=CODEX_RUNTIME_RECOVERY_NPM_RUNTIME_BINARY_MISSING" in report
    assert "/" not in report.split("reason=", 1)[1].splitlines()[0]


def test_codegen_runtime_recovery_reports_provider_unavailable_success(monkeypatch) -> None:
    monkeypatch.setattr(
        maintenance,
        "recover_pinned_codex_runtime",
        lambda _environment: type(
            "Result", (), {"success": True, "reason": "ready_provider_unavailable"}
        )(),
    )
    monkeypatch.setattr(maintenance, "pinned_codex_runtime_path", lambda _environment: "/canonical/npm/bin/codex")
    monkeypatch.setattr(
        maintenance,
        "_run_fixed",
        lambda argv, *, timeout=60, cwd=None: subprocess.CompletedProcess(
            argv, 0, f"codex-cli {maintenance.TARGET_CODEX_VERSION}\n", ""
        ),
    )
    report = maintenance._recover_codegen_runtime()
    assert "reason=CODEX_RUNTIME_RECOVERED_PROVIDER_UNAVAILABLE" in report


def test_codegen_runtime_recovery_fails_closed_on_unverified_version(monkeypatch) -> None:
    monkeypatch.setattr(
        maintenance,
        "recover_pinned_codex_runtime",
        lambda _environment: type("Result", (), {"success": True, "reason": "ready"})(),
    )
    monkeypatch.setattr(maintenance, "pinned_codex_runtime_path", lambda _environment: "/canonical/npm/bin/codex")
    monkeypatch.setattr(
        maintenance,
        "_run_fixed",
        lambda argv, *, timeout=60, cwd=None: subprocess.CompletedProcess(argv, 0, "codex-cli 0.125.0\n", ""),
    )
    report = maintenance._recover_codegen_runtime()
    assert report.startswith("BLOCKED:")
    assert "reason=CODEX_RUNTIME_VERSION_UNVERIFIED" in report


def _install_pinned_canary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(maintenance, "pinned_codex_runtime_path", lambda _environment: "/canonical/npm/bin/codex")


def _assert_fixed_canary_model(argv: list[str]) -> None:
    assert "--model" in argv
    model_index = argv.index("--model")
    assert argv[model_index + 1] == maintenance.TARGET_CODEX_MODEL


def test_codegen_canary_does_not_fallback_for_exact_model_metadata_decoder_failure(monkeypatch) -> None:
    calls: list[list[str]] = []
    _install_pinned_canary(monkeypatch)

    def fake_which(name: str, *, path: str | None = None) -> str | None:
        return "/trusted/openhands" if name == "openhands" else None

    def fake_run(argv: list[str], *, timeout: int = 60, cwd: str | None = None):
        calls.append(argv)
        if argv[:3] == ["git", "init", "-q"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == "/canonical/npm/bin/codex":
            _assert_fixed_canary_model(argv)
            return subprocess.CompletedProcess(argv, 1, "", "failed to decode models response: unknown variant `max`")
        raise AssertionError("metadata incompatibility must not fall back")

    monkeypatch.setattr(maintenance.shutil, "which", fake_which)
    monkeypatch.setattr(maintenance, "_run_fixed", fake_run)
    report = maintenance._codegen_read_only_canary()
    assert "reason=CODEX_CANARY_FAILED" in report
    assert not any(argv[0] == "/trusted/openhands" for argv in calls)


def test_codegen_canary_still_falls_back_for_provider_quota(monkeypatch) -> None:
    calls: list[list[str]] = []
    _install_pinned_canary(monkeypatch)

    def fake_which(name: str, *, path: str | None = None) -> str | None:
        return "/trusted/openhands" if name == "openhands" else None

    def fake_run(argv: list[str], *, timeout: int = 60, cwd: str | None = None):
        calls.append(argv)
        if argv[:3] == ["git", "init", "-q"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == "/canonical/npm/bin/codex":
            _assert_fixed_canary_model(argv)
            return subprocess.CompletedProcess(argv, 1, "", "usage limit reached")
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
    _install_pinned_canary(monkeypatch)

    def fake_which(name: str, *, path: str | None = None) -> str | None:
        return "/trusted/openhands" if name == "openhands" else None

    def fake_run(argv: list[str], *, timeout: int = 60, cwd: str | None = None):
        calls.append(argv)
        if argv[:3] == ["git", "init", "-q"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == "/canonical/npm/bin/codex":
            _assert_fixed_canary_model(argv)
            return subprocess.CompletedProcess(argv, 9, "", "unrelated synthetic codex failure")
        raise AssertionError("fallback must not run")

    monkeypatch.setattr(maintenance.shutil, "which", fake_which)
    monkeypatch.setattr(maintenance, "_run_fixed", fake_run)
    report = maintenance._codegen_read_only_canary()
    assert "reason=CODEX_CANARY_FAILED" in report
    assert not any(argv[0] == "/trusted/openhands" for argv in calls)


def test_codegen_canary_fails_closed_when_pinned_runtime_cannot_be_verified(monkeypatch) -> None:
    monkeypatch.setattr(
        maintenance,
        "pinned_codex_runtime_path",
        lambda _environment: (_ for _ in ()).throw(
            maintenance.CodexRuntimeRecoveryError("codex_runtime_version_mismatch")
        ),
    )
    monkeypatch.setattr(
        maintenance,
        "_run_fixed",
        lambda argv, *, timeout=60, cwd=None: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    report = maintenance._codegen_read_only_canary()
    assert "reason=CODEX_CANARY_RUNTIME_UNVERIFIED" in report


def test_control_plane_recovery_wires_fixed_actions_without_hermes_substitution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
    assert calls == ["codegen_runtime_recover", "codegen_read_only_canary", "queue_reactivate"]
    assert runner.HERMES_WORKER_PREFLIGHT not in calls
