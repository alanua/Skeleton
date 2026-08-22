from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

import core.runner_repository_maintenance_executor as maintenance
from core.runner_repository_maintenance_executor import (
    BUILD_AND_LOCAL_OTA_OPERATION,
    LAVALAMP_APPROVAL_REFERENCE,
    LAVALAMP_FIRMWARE_NAME,
    LAVALAMP_IDEMPOTENCY_KEY,
    LAVALAMP_MANIFEST_NAME,
    LAVALAMP_PLATFORMIO_ENV,
    LAVALAMP_REPOSITORY,
    LAVALAMP_SOURCE_BRANCH,
    LAVALAMP_SOURCE_REPOSITORY,
    LAVALAMP_SOURCE_SHA,
    LAVALAMP_WLED_REPOSITORY,
    LAVALAMP_WLED_SHA,
    RepositoryMaintenanceExecutor,
    RegisteredMaintenanceActionError,
    RegisteredMaintenanceExecutor,
    registered_maintenance_task_id,
)
from core.runner_task import RunnerTask
from scripts import runner_poll_github_tasks as runner


ROOT = Path(__file__).resolve().parents[1]


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


def _snapshot_issue_body(expected_sha: str = "a" * 40, *, extra: str = "") -> str:
    lines = [
        "Mode: RUNTIME_MAINTENANCE_TASK",
        f"Maintenance Task ID: {maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_TASK_ID}",
        f"Repository: {maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_REPOSITORY}",
        f"Expected Main SHA: {expected_sha}",
        f"Target: {maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_TARGET}",
    ]
    if extra:
        lines.append(extra)
    return "\n".join(lines)


def _snapshot_repo(tmp_path: Path, *, installer: bytes | None = None) -> Path:
    repo = tmp_path / "Skeleton"
    repo.mkdir()
    (repo / ".git").mkdir()
    for relative in (
        maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_INSTALLER_REL,
        maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_PAYLOAD_REL,
        maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_WRAPPER_REL,
        maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_CONTRACT_REL,
    ):
        source = ROOT / relative
        destination = repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if relative == maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_INSTALLER_REL and installer is not None:
            destination.write_bytes(installer)
            os.chmod(destination, 0o755)
        else:
            shutil.copy2(source, destination)
            os.chmod(destination, 0o755 if os.access(source, os.X_OK) else 0o644)
    return repo


def _install_git_and_sudo_runner(
    monkeypatch: pytest.MonkeyPatch,
    *,
    expected_sha: str = "a" * 40,
    copy_returncode: int = 0,
    execute_returncode: int = 0,
    extra_argv_returncode: int = 2,
) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], *, timeout: int = 60, cwd: str | None = None):
        calls.append(argv)
        if argv[:2] == [maintenance._GIT_BIN, "config"]:
            return subprocess.CompletedProcess(argv, 0, "https://github.com/alanua/Skeleton.git\n", "")
        if argv[:2] == [maintenance._GIT_BIN, "branch"]:
            return subprocess.CompletedProcess(argv, 0, "main\n", "")
        if argv[:2] == [maintenance._GIT_BIN, "rev-parse"]:
            return subprocess.CompletedProcess(argv, 0, expected_sha + "\n", "")
        if argv[:2] == [maintenance._GIT_BIN, "status"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == [maintenance._SUDO_BIN, "-n", maintenance._INSTALL_BIN]:
            staged = Path(argv[-2])
            assert staged.is_file()
            assert not staged.is_symlink()
            assert maintenance._git_blob_sha1(staged.read_bytes()) == (
                maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_INSTALLER_BLOB_SHA
            )
            assert (staged.stat().st_mode & 0o777) == 0o600
            return subprocess.CompletedProcess(argv, copy_returncode, "", "")
        if argv[:3] == [
            maintenance._SUDO_BIN,
            "-n",
            str(maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_PROTECTED_INSTALLER),
        ]:
            return subprocess.CompletedProcess(argv, execute_returncode, "DONE\n", "")
        if argv[:3] == [
            maintenance._SUDO_BIN,
            "-n",
            str(maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_PROTECTED_SIGNER),
        ]:
            return subprocess.CompletedProcess(argv, extra_argv_returncode, "", "")
        raise AssertionError(argv)

    monkeypatch.setattr(maintenance, "_run_fixed", fake_run)
    return calls


def test_home_edge_snapshot_signer_primitive_uses_exact_copy_and_execute_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _snapshot_repo(tmp_path)
    calls = _install_git_and_sudo_runner(monkeypatch)
    protected_checks: list[Path] = []
    monkeypatch.setattr(
        maintenance,
        "_verify_protected_regular_file",
        lambda path, **_kwargs: protected_checks.append(path),
    )

    result = RegisteredMaintenanceExecutor(lambda *_args: "BLOCKED", str(repo)).install_home_edge_media_source_snapshot_signer(
        _snapshot_issue_body()
    )

    assert result == maintenance.HomeEdgeSnapshotSignerInstallResult("DONE", "SIGNER_INSTALL_READY")
    copy_calls = [call for call in calls if call[:3] == [maintenance._SUDO_BIN, "-n", maintenance._INSTALL_BIN]]
    assert len(copy_calls) == 1
    assert copy_calls[0][3:9] == ["-D", "-o", "root", "-g", "root", "-m"]
    assert copy_calls[0][9] == "0555"
    assert copy_calls[0][-1] == str(maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_PROTECTED_INSTALLER)
    staged = Path(copy_calls[0][-2])
    assert not staged.exists()
    assert not staged.parent.exists()
    assert [
        maintenance._SUDO_BIN,
        "-n",
        str(maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_PROTECTED_INSTALLER),
        "--repo-root",
        str(repo),
    ] in calls
    assert [
        maintenance._SUDO_BIN,
        "-n",
        str(maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_PROTECTED_SIGNER),
        "--unexpected-argv",
    ] in calls
    assert protected_checks == [
        maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_PROTECTED_INSTALLER,
        maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_PROTECTED_PAYLOAD,
        maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_PROTECTED_SIGNER,
        maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_PROTECTED_CONTRACT,
    ]


def test_home_edge_snapshot_signer_rejects_arbitrary_fields_before_sudo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _snapshot_repo(tmp_path)
    calls = _install_git_and_sudo_runner(monkeypatch)

    result = RegisteredMaintenanceExecutor(lambda *_args: "BLOCKED", str(repo)).install_home_edge_media_source_snapshot_signer(
        _snapshot_issue_body(extra="Path: /tmp/evil")
    )

    assert result.status == "BLOCKED"
    assert result.reason == "UNKNOWN_RUNTIME_INPUT_FIELD"
    assert not any(call[0] == maintenance._SUDO_BIN for call in calls)


@pytest.mark.parametrize(
    ("git_status", "reason"),
    [
        ("wrong_branch", "CANONICAL_BRANCH_MISMATCH"),
        ("wrong_main", "CANONICAL_MAIN_SHA_MISMATCH"),
        ("dirty", "CANONICAL_CHECKOUT_DIRTY"),
    ],
)
def test_home_edge_snapshot_signer_blocks_stale_or_dirty_checkout_before_sudo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, git_status: str, reason: str
) -> None:
    repo = _snapshot_repo(tmp_path)
    calls: list[list[str]] = []

    def fake_run(argv: list[str], *, timeout: int = 60, cwd: str | None = None):
        calls.append(argv)
        if argv[:2] == [maintenance._GIT_BIN, "config"]:
            return subprocess.CompletedProcess(argv, 0, "https://github.com/alanua/Skeleton.git\n", "")
        if argv[:2] == [maintenance._GIT_BIN, "branch"]:
            branch = "runner/issue" if git_status == "wrong_branch" else "main"
            return subprocess.CompletedProcess(argv, 0, branch + "\n", "")
        if argv[:2] == [maintenance._GIT_BIN, "rev-parse"]:
            sha = "b" * 40 if git_status == "wrong_main" else "a" * 40
            return subprocess.CompletedProcess(argv, 0, sha + "\n", "")
        if argv[:2] == [maintenance._GIT_BIN, "status"]:
            output = " M file\n" if git_status == "dirty" else ""
            return subprocess.CompletedProcess(argv, 0, output, "")
        raise AssertionError(argv)

    monkeypatch.setattr(maintenance, "_run_fixed", fake_run)
    result = RegisteredMaintenanceExecutor(lambda *_args: "BLOCKED", str(repo)).install_home_edge_media_source_snapshot_signer(
        _snapshot_issue_body()
    )

    assert result == maintenance.HomeEdgeSnapshotSignerInstallResult("BLOCKED", reason)
    assert not any(call[0] == maintenance._SUDO_BIN for call in calls)


def test_home_edge_snapshot_signer_blocks_blob_mismatch_before_sudo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _snapshot_repo(tmp_path, installer=b"tampered\n")
    calls = _install_git_and_sudo_runner(monkeypatch)

    result = RegisteredMaintenanceExecutor(lambda *_args: "BLOCKED", str(repo)).install_home_edge_media_source_snapshot_signer(
        _snapshot_issue_body()
    )

    assert result.status == "BLOCKED"
    assert result.reason == "SOURCE_BLOB_MISMATCH"
    assert not any(call[0] == maintenance._SUDO_BIN for call in calls)


def test_home_edge_snapshot_signer_blocks_source_symlink_before_sudo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _snapshot_repo(tmp_path)
    installer = repo / maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_INSTALLER_REL
    installer.unlink()
    installer.symlink_to(ROOT / maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_INSTALLER_REL)
    calls = _install_git_and_sudo_runner(monkeypatch)

    result = RegisteredMaintenanceExecutor(lambda *_args: "BLOCKED", str(repo)).install_home_edge_media_source_snapshot_signer(
        _snapshot_issue_body()
    )

    assert result.status == "BLOCKED"
    assert result.reason == "SOURCE_BLOB_UNSAFE"
    assert not any(call[0] == maintenance._SUDO_BIN for call in calls)


def test_home_edge_snapshot_signer_copy_unavailable_does_not_execute_installer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _snapshot_repo(tmp_path)
    calls = _install_git_and_sudo_runner(monkeypatch, copy_returncode=1)

    result = RegisteredMaintenanceExecutor(lambda *_args: "BLOCKED", str(repo)).install_home_edge_media_source_snapshot_signer(
        _snapshot_issue_body()
    )

    assert result == maintenance.HomeEdgeSnapshotSignerInstallResult("NEEDS_OPERATOR", "PRIVILEGE_UNAVAILABLE")
    assert not any(
        call[:3]
        == [
            maintenance._SUDO_BIN,
            "-n",
            str(maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_PROTECTED_INSTALLER),
        ]
        for call in calls
    )


def test_home_edge_snapshot_signer_protected_hash_mismatch_does_not_execute_installer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _snapshot_repo(tmp_path)
    calls = _install_git_and_sudo_runner(monkeypatch)

    def fake_protected(_path: Path, **_kwargs: object) -> None:
        raise maintenance.RepositoryMaintenanceBlocked("PROTECTED_COPY_BLOB_MISMATCH")

    monkeypatch.setattr(maintenance, "_verify_protected_regular_file", fake_protected)
    result = RegisteredMaintenanceExecutor(lambda *_args: "BLOCKED", str(repo)).install_home_edge_media_source_snapshot_signer(
        _snapshot_issue_body()
    )

    assert result == maintenance.HomeEdgeSnapshotSignerInstallResult("BLOCKED", "PROTECTED_COPY_BLOB_MISMATCH")
    assert not any(
        call[:3]
        == [
            maintenance._SUDO_BIN,
            "-n",
            str(maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_PROTECTED_INSTALLER),
        ]
        for call in calls
    )


def test_home_edge_snapshot_signer_installer_failure_has_no_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _snapshot_repo(tmp_path)
    calls = _install_git_and_sudo_runner(monkeypatch, execute_returncode=2)
    monkeypatch.setattr(maintenance, "_verify_protected_regular_file", lambda _path, **_kwargs: None)

    result = RegisteredMaintenanceExecutor(lambda *_args: "BLOCKED", str(repo)).install_home_edge_media_source_snapshot_signer(
        _snapshot_issue_body()
    )

    assert result == maintenance.HomeEdgeSnapshotSignerInstallResult("NEEDS_OPERATOR", "INSTALLER_NEEDS_OPERATOR")
    execute_calls = [
        call
        for call in calls
        if call[:3]
        == [
            maintenance._SUDO_BIN,
            "-n",
            str(maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_PROTECTED_INSTALLER),
        ]
    ]
    assert len(execute_calls) == 1
    assert not any(
        call[:3]
        == [
            maintenance._SUDO_BIN,
            "-n",
            str(maintenance.HOME_EDGE_MEDIA_SOURCE_SNAPSHOT_PROTECTED_SIGNER),
        ]
        for call in calls
    )


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


def _lavalamp_task(**overrides: object) -> RunnerTask:
    payload: dict[str, object] = {
        "operation": BUILD_AND_LOCAL_OTA_OPERATION,
        "project": "lavalamp",
        "repository": LAVALAMP_REPOSITORY,
        "source_branch": LAVALAMP_SOURCE_BRANCH,
        "source_sha": LAVALAMP_SOURCE_SHA,
        "wled_commit": LAVALAMP_WLED_SHA,
        "environment": LAVALAMP_PLATFORMIO_ENV,
        "artifact_root": str(maintenance.LAVALAMP_ARTIFACT_ROOT),
        "relay": "home-edge-01",
        "target": "192.168.1.164",
        "approval_reference": LAVALAMP_APPROVAL_REFERENCE,
        "idempotency_key": LAVALAMP_IDEMPOTENCY_KEY,
        "required_effects": ["CY Anemone", "CY Tidal Bloom"],
    }
    mapping: dict[str, object] = {
        "schema": "skeleton.runner_task.v1",
        "repo": LAVALAMP_REPOSITORY,
        "branch": LAVALAMP_SOURCE_BRANCH,
        "base_sha": LAVALAMP_SOURCE_SHA,
        "task_kind": "repository_maintenance",
        "payload": payload,
        "requested_capabilities": ["repository_maintenance", "repository_read", "test_execution"],
        "allowed_files": [LAVALAMP_FIRMWARE_NAME, LAVALAMP_MANIFEST_NAME],
        "forbidden_actions": ["no direct LAN OTA implementation"],
        "validation_commands": [["python3", "-m", "pytest", "-q"]],
        "validation_timeout_seconds": 900,
        "expected_output": ["deterministic lavalamp executor"],
        "privacy_boundary": "PUBLIC_SAFE_REPOSITORY_ONLY",
        "approval_reference": LAVALAMP_APPROVAL_REFERENCE,
        "idempotency_key": LAVALAMP_IDEMPOTENCY_KEY,
    }
    for key, value in overrides.items():
        if key.startswith("payload__"):
            payload[key.split("__", 1)[1]] = value
        else:
            mapping[key] = value
    return RunnerTask.from_mapping(mapping)


def _source_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "Lavalamp"
    _populate_source_checkout(checkout)
    return checkout


def _populate_source_checkout(checkout: Path) -> None:
    (checkout / "overlays/wled/usermods/cylinder_lava").mkdir(parents=True)
    (checkout / "overlays/wled/platformio_override.ini").write_text(
        "[env:cylinder_lava_esp32]\n", encoding="utf-8"
    )
    (checkout / "overlays/wled/usermods/cylinder_lava/mod.cpp").write_text(
        "cylinder_lava CY Anemone CY Tidal Bloom\n", encoding="utf-8"
    )
    (checkout / "patches").mkdir()
    (checkout / "patches/wled-usermods-list-cylinder-lava.patch").write_text(
        "patch\n", encoding="utf-8"
    )


def _project_tree(path: Path, checkout: Path) -> Path:
    project_tree = path / "PROJECT_TREE.yaml"
    project_tree.write_text(
        "\n".join(
            (
                'version: "1.0.0"',
                "default_project: lavalamp",
                "projects:",
                "  lavalamp:",
                "    repo: alanua/Lavalamp",
                f"    checkout_path: {checkout}",
                f"    worktree_root: {path / 'worktrees'}",
                "    public: true",
                "    runner_enabled: true",
                "    execution_modes:",
                "      planning_only: false",
                "      codex_issue_worktree: true",
                "      live_cross_repo: false",
                "    requires_explicit_approval_for_mode_change: true",
                "    future_parallel_worktrees: true",
                "    runtime_approval_required: true",
                "    worktree_name_prefix: lavalamp",
            )
        ),
        encoding="utf-8",
    )
    return project_tree


class FakeUrlOpenResponse:
    def __init__(self, payload: bytes) -> None:
        self.stream = io.BytesIO(payload)

    def __enter__(self) -> "FakeUrlOpenResponse":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.stream.read(size)


def _fake_node_archive() -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:xz") as archive:
        for name in ("node", "npm"):
            data = b"#!/bin/sh\n"
            member = tarfile.TarInfo(f"node-v20.20.2-linux-x64/bin/{name}")
            member.mode = 0o755
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return stream.getvalue()


def _install_fake_node_download(monkeypatch: pytest.MonkeyPatch, payload: bytes | None = None) -> bytes:
    archive = payload if payload is not None else _fake_node_archive()
    monkeypatch.setattr(
        maintenance.urllib.request,
        "urlopen",
        lambda url, timeout: FakeUrlOpenResponse(archive),
    )
    monkeypatch.setattr(maintenance, "LAVALAMP_NODE_SHA256", hashlib.sha256(archive).hexdigest())
    return archive


def _path_traversal_node_archive() -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:xz") as archive:
        data = b"escape"
        member = tarfile.TarInfo("../escaped-node-file")
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    return stream.getvalue()


class FakeFirmwareAction:
    def __init__(self) -> None:
        self.executions = 0
        self.postflights = 0

    def execute(self, request: object) -> dict[str, object]:
        self.executions += 1
        return {
            "final_status": "DONE",
            "effects": {"CY Anemone": True, "CY Tidal Bloom": True},
        }

    def verify_postflight_only(self, request: object) -> dict[str, object]:
        self.postflights += 1
        return {
            "final_status": "DONE",
            "effects": {"CY Anemone": True, "CY Tidal Bloom": True},
        }


class FakeRepositoryRunner:
    def __init__(
        self,
        *,
        source_origin: str = LAVALAMP_SOURCE_REPOSITORY,
        source_head: str = LAVALAMP_SOURCE_SHA,
    ) -> None:
        self.calls: list[tuple[list[str], Path | None, int | None, object]] = []
        self.source_origin = source_origin
        self.source_head = source_head

    def __call__(
        self,
        args: list[str],
        cwd: Path | None,
        timeout: int | None,
        env: object,
    ) -> tuple[int, str]:
        self.calls.append((args, cwd, timeout, env))
        if args[:3] == ["git", "config", "--get"]:
            return 0, (
                LAVALAMP_WLED_REPOSITORY + "\n"
                if cwd and cwd.name == "WLED"
                else self.source_origin + "\n"
            )
        if args[:2] == ["git", "branch"]:
            return 0, "\n" if cwd and cwd.name == "Lavalamp-source" else "main\n"
        if args[:2] == ["git", "rev-parse"]:
            if cwd and cwd.name == "WLED":
                return 0, LAVALAMP_WLED_SHA + "\n"
            return 0, self.source_head + "\n"
        if args[:2] == ["git", "status"]:
            return 0, ""
        if args[:2] == ["git", "init"]:
            Path(args[2]).mkdir(parents=True)
            return 0, ""
        if args[:3] == ["git", "remote", "add"] or args[:2] == ["git", "fetch"]:
            return 0, ""
        if args[:2] == ["git", "checkout"]:
            assert cwd is not None
            if cwd.name == "Lavalamp-source":
                _populate_source_checkout(cwd)
            if cwd.name == "WLED":
                (cwd / "usermods").mkdir(parents=True, exist_ok=True)
            return 0, ""
        if args[:2] == ["git", "apply"]:
            assert cwd is not None
            (cwd / "usermods_list.cpp").write_text(
                "cylinder_lava CY Anemone CY Tidal Bloom\n", encoding="utf-8"
            )
            return 0, ""
        if args[:3] == [sys.executable, "-m", "pip"]:
            assert env is not None
            pip_target = Path(str(env["PIP_TARGET"]))
            assert pip_target.parent.name == "tools"
            assert pip_target.parent.parent.name.startswith("build-")
            assert Path(str(env["PIP_CACHE_DIR"])).parent == pip_target.parent.parent
            Path(str(env["PIP_TARGET"])).mkdir(parents=True, exist_ok=True)
            return 0, ""
        if Path(args[0]).name in {"node", "npm"}:
            assert env is not None
            assert "node-v20.20.2-linux-x64/bin" in str(env["PATH"]).split(":")[0]
            return 0, "v20.20.2\n" if Path(args[0]).name == "node" else "10.8.2\n"
        if args == [sys.executable, "-m", "platformio", "run", "-e", LAVALAMP_PLATFORMIO_ENV]:
            assert cwd is not None
            assert env is not None
            assert str(env["PYTHONPATH"]).startswith(str(cwd.parent / "tools" / "platformio"))
            assert str(env["PATH"]).split(":")[0].startswith(str(cwd.parent / "tools" / "node"))
            assert str(env["NPM_CONFIG_CACHE"]).startswith(str(cwd.parent))
            firmware = cwd / ".pio/build" / LAVALAMP_PLATFORMIO_ENV / LAVALAMP_FIRMWARE_NAME
            firmware.parent.mkdir(parents=True)
            firmware.write_bytes(b"firmware-image")
            return 0, ""
        return 1, "unexpected"


def test_lavalamp_mismatch_blocks_before_commands(tmp_path: Path) -> None:
    action = FakeFirmwareAction()
    calls: list[list[str]] = []
    executor = RepositoryMaintenanceExecutor(
        project_tree_path=tmp_path / "missing.yaml",
        run_command=lambda args, cwd, timeout, env: (calls.append(args) or (0, "")),
        firmware_action=action,
    )

    code, output = executor.execute(_lavalamp_task(payload__source_sha="0" * 40))

    assert code == 0
    assert "RESULT: BLOCKED" in output
    assert "SOURCE_SHA_MISMATCH" in output
    assert calls == []
    assert action.executions == 0


def test_lavalamp_build_retains_only_firmware_and_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact_root = tmp_path / "artifacts/lavalamp/issue-1922-c98acbf"
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(maintenance, "LAVALAMP_APPROVED_ARTIFACT_PARENT", tmp_path / "artifacts/lavalamp")
    monkeypatch.setattr(maintenance, "LAVALAMP_ARTIFACT_ROOT", artifact_root)
    _install_fake_node_download(monkeypatch)
    checkout = _source_checkout(tmp_path)
    fake = FakeRepositoryRunner()
    action = FakeFirmwareAction()

    code, output = RepositoryMaintenanceExecutor(
        project_tree_path=_project_tree(tmp_path, checkout),
        run_command=fake,
        firmware_action=action,
    ).execute(_lavalamp_task(payload__artifact_root=str(artifact_root)))

    assert code == 0
    assert output.startswith("RESULT: DONE")
    assert sorted(path.name for path in artifact_root.iterdir()) == [
        LAVALAMP_FIRMWARE_NAME,
        LAVALAMP_MANIFEST_NAME,
    ]
    manifest = json.loads((artifact_root / LAVALAMP_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["source_sha"] == LAVALAMP_SOURCE_SHA
    assert manifest["wled_commit"] == LAVALAMP_WLED_SHA
    assert manifest["environment"] == LAVALAMP_PLATFORMIO_ENV
    assert manifest["cleanup_status"] == "complete"
    assert manifest["approval_reference"] == LAVALAMP_APPROVAL_REFERENCE
    assert action.executions == 1
    assert not any(call[1] == checkout for call in fake.calls)
    assert any(
        call[0] == ["git", "fetch", "--depth", "1", "origin", LAVALAMP_SOURCE_SHA]
        and call[1] is not None
        and call[1].name == "Lavalamp-source"
        and str(call[1]).startswith(str(artifact_root))
        for call in fake.calls
    )
    assert any(call[0] == ["git", "fetch", "--depth", "1", "origin", LAVALAMP_WLED_SHA] for call in fake.calls)
    build_calls = [
        call for call in fake.calls
        if call[0] == [sys.executable, "-m", "platformio", "run", "-e", LAVALAMP_PLATFORMIO_ENV]
    ]
    assert len(build_calls) == 1
    assert str(build_calls[0][3]["PYTHONPATH"]).startswith(str(artifact_root))
    assert str(build_calls[0][3]["PATH"]).split(":")[0].startswith(str(artifact_root))
    assert not any(path.name.startswith("build-") for path in artifact_root.iterdir())


def test_lavalamp_shared_checkout_head_is_not_historical_source_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact_root = tmp_path / "artifacts/lavalamp/issue-1922-c98acbf"
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(maintenance, "LAVALAMP_APPROVED_ARTIFACT_PARENT", tmp_path / "artifacts/lavalamp")
    monkeypatch.setattr(maintenance, "LAVALAMP_ARTIFACT_ROOT", artifact_root)
    _install_fake_node_download(monkeypatch)
    shared_checkout = tmp_path / "registered-lavalamp"
    fake = FakeRepositoryRunner()
    action = FakeFirmwareAction()

    code, output = RepositoryMaintenanceExecutor(
        project_tree_path=_project_tree(tmp_path, shared_checkout),
        run_command=fake,
        firmware_action=action,
    ).execute(_lavalamp_task(payload__artifact_root=str(artifact_root)))

    assert code == 0
    assert output.startswith("RESULT: DONE")
    assert action.executions == 1
    assert not any(call[1] == shared_checkout for call in fake.calls)
    assert not any(
        call[1] == shared_checkout
        and (
            call[0][:2] == ["git", "checkout"]
            or call[0][:2] in (["git", "reset"], ["git", "stash"], ["git", "clean"])
        )
        for call in fake.calls
    )


def test_lavalamp_platformio_bootstrap_failure_returns_stable_reason_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact_root = tmp_path / "artifacts/lavalamp/issue-1922-c98acbf"
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(maintenance, "LAVALAMP_APPROVED_ARTIFACT_PARENT", tmp_path / "artifacts/lavalamp")
    monkeypatch.setattr(maintenance, "LAVALAMP_ARTIFACT_ROOT", artifact_root)
    _install_fake_node_download(monkeypatch)
    checkout = _source_checkout(tmp_path)
    fake = FakeRepositoryRunner()
    action = FakeFirmwareAction()

    def fail_pip(args: list[str], cwd: Path | None, timeout: int | None, env: object) -> tuple[int, str]:
        if args[:3] == [sys.executable, "-m", "pip"]:
            return 2, "synthetic pip failure"
        return fake(args, cwd, timeout, env)

    code, output = RepositoryMaintenanceExecutor(
        project_tree_path=_project_tree(tmp_path, checkout),
        run_command=fail_pip,
        firmware_action=action,
    ).execute(_lavalamp_task(payload__artifact_root=str(artifact_root)))

    assert code == 0
    assert "RESULT: BLOCKED" in output
    assert '"reason": "PLATFORMIO_BOOTSTRAP_FAILED"' in output
    assert action.executions == 0
    assert not any(call[0] == [sys.executable, "-m", "platformio", "run", "-e", LAVALAMP_PLATFORMIO_ENV] for call in fake.calls)
    assert not any(path.name.startswith("build-") for path in artifact_root.iterdir())


def test_lavalamp_missing_pip_executable_returns_stable_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact_root = tmp_path / "artifacts/lavalamp/issue-1922-c98acbf"
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(maintenance, "LAVALAMP_APPROVED_ARTIFACT_PARENT", tmp_path / "artifacts/lavalamp")
    monkeypatch.setattr(maintenance, "LAVALAMP_ARTIFACT_ROOT", artifact_root)
    _install_fake_node_download(monkeypatch)
    checkout = _source_checkout(tmp_path)
    fake = FakeRepositoryRunner()
    action = FakeFirmwareAction()

    def missing_pip(args: list[str], cwd: Path | None, timeout: int | None, env: object) -> tuple[int, str]:
        if args[:3] == [sys.executable, "-m", "pip"]:
            raise FileNotFoundError("python missing")
        return fake(args, cwd, timeout, env)

    _code, output = RepositoryMaintenanceExecutor(
        project_tree_path=_project_tree(tmp_path, checkout),
        run_command=missing_pip,
        firmware_action=action,
    ).execute(_lavalamp_task(payload__artifact_root=str(artifact_root)))

    assert "RESULT: BLOCKED" in output
    assert '"reason": "PYTHON_PIP_UNAVAILABLE"' in output
    assert "REPOSITORY_MAINTENANCE_FAILED" not in output
    assert action.executions == 0
    assert not any(path.name.startswith("build-") for path in artifact_root.iterdir())


def test_lavalamp_node_checksum_mismatch_blocks_before_build_or_home_edge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact_root = tmp_path / "artifacts/lavalamp/issue-1922-c98acbf"
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(maintenance, "LAVALAMP_APPROVED_ARTIFACT_PARENT", tmp_path / "artifacts/lavalamp")
    monkeypatch.setattr(maintenance, "LAVALAMP_ARTIFACT_ROOT", artifact_root)
    monkeypatch.setattr(
        maintenance.urllib.request,
        "urlopen",
        lambda url, timeout: FakeUrlOpenResponse(b"not the expected archive"),
    )
    monkeypatch.setattr(maintenance, "LAVALAMP_NODE_SHA256", "0" * 64)
    checkout = _source_checkout(tmp_path)
    fake = FakeRepositoryRunner()
    action = FakeFirmwareAction()

    _code, output = RepositoryMaintenanceExecutor(
        project_tree_path=_project_tree(tmp_path, checkout),
        run_command=fake,
        firmware_action=action,
    ).execute(_lavalamp_task(payload__artifact_root=str(artifact_root)))

    assert "RESULT: BLOCKED" in output
    assert '"reason": "NODE_CHECKSUM_MISMATCH"' in output
    assert action.executions == 0
    assert not any(call[0][:3] == [sys.executable, "-m", "pip"] for call in fake.calls)
    assert not any(call[0] == [sys.executable, "-m", "platformio", "run", "-e", LAVALAMP_PLATFORMIO_ENV] for call in fake.calls)
    assert not any(path.name.startswith("build-") for path in artifact_root.iterdir())


def test_lavalamp_unsupported_node_host_blocks_before_download_build_or_ota(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact_root = tmp_path / "artifacts/lavalamp/issue-1922-c98acbf"
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(maintenance, "LAVALAMP_APPROVED_ARTIFACT_PARENT", tmp_path / "artifacts/lavalamp")
    monkeypatch.setattr(maintenance, "LAVALAMP_ARTIFACT_ROOT", artifact_root)
    monkeypatch.setattr(maintenance.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(maintenance.platform, "machine", lambda: "arm64")
    downloads: list[str] = []
    monkeypatch.setattr(
        maintenance.urllib.request,
        "urlopen",
        lambda url, timeout: downloads.append(url) or FakeUrlOpenResponse(b""),
    )
    checkout = _source_checkout(tmp_path)
    fake = FakeRepositoryRunner()
    action = FakeFirmwareAction()

    _code, output = RepositoryMaintenanceExecutor(
        project_tree_path=_project_tree(tmp_path, checkout),
        run_command=fake,
        firmware_action=action,
    ).execute(_lavalamp_task(payload__artifact_root=str(artifact_root)))

    assert "RESULT: BLOCKED" in output
    assert '"reason": "NODE_UNSUPPORTED_HOST"' in output
    assert downloads == []
    assert action.executions == 0
    assert not any(call[0][:3] == [sys.executable, "-m", "pip"] for call in fake.calls)
    assert not any(call[0] == [sys.executable, "-m", "platformio", "run", "-e", LAVALAMP_PLATFORMIO_ENV] for call in fake.calls)
    assert not any(path.name.startswith("build-") for path in artifact_root.iterdir())


def test_lavalamp_node_extraction_cannot_escape_artifact_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact_root = tmp_path / "artifacts/lavalamp/issue-1922-c98acbf"
    archive = _path_traversal_node_archive()
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(maintenance, "LAVALAMP_APPROVED_ARTIFACT_PARENT", tmp_path / "artifacts/lavalamp")
    monkeypatch.setattr(maintenance, "LAVALAMP_ARTIFACT_ROOT", artifact_root)
    _install_fake_node_download(monkeypatch, archive)
    checkout = _source_checkout(tmp_path)
    fake = FakeRepositoryRunner()
    action = FakeFirmwareAction()

    _code, output = RepositoryMaintenanceExecutor(
        project_tree_path=_project_tree(tmp_path, checkout),
        run_command=fake,
        firmware_action=action,
    ).execute(_lavalamp_task(payload__artifact_root=str(artifact_root)))

    assert "RESULT: BLOCKED" in output
    assert '"reason": "NODE_EXTRACTION_BLOCKED"' in output
    assert not (artifact_root / "escaped-node-file").exists()
    assert action.executions == 0
    assert not any(path.name.startswith("build-") for path in artifact_root.iterdir())


def test_lavalamp_node_runtime_filenotfound_returns_stable_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact_root = tmp_path / "artifacts/lavalamp/issue-1922-c98acbf"
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(maintenance, "LAVALAMP_APPROVED_ARTIFACT_PARENT", tmp_path / "artifacts/lavalamp")
    monkeypatch.setattr(maintenance, "LAVALAMP_ARTIFACT_ROOT", artifact_root)
    _install_fake_node_download(monkeypatch)
    checkout = _source_checkout(tmp_path)
    fake = FakeRepositoryRunner()
    action = FakeFirmwareAction()

    def missing_node(args: list[str], cwd: Path | None, timeout: int | None, env: object) -> tuple[int, str]:
        if args and Path(args[0]).name == "node":
            raise FileNotFoundError("node missing")
        return fake(args, cwd, timeout, env)

    _code, output = RepositoryMaintenanceExecutor(
        project_tree_path=_project_tree(tmp_path, checkout),
        run_command=missing_node,
        firmware_action=action,
    ).execute(_lavalamp_task(payload__artifact_root=str(artifact_root)))

    assert "RESULT: BLOCKED" in output
    assert '"reason": "NODE_RUNTIME_UNAVAILABLE"' in output
    assert "REPOSITORY_MAINTENANCE_FAILED" not in output
    assert action.executions == 0
    assert not any(path.name.startswith("build-") for path in artifact_root.iterdir())


@pytest.mark.parametrize(
    ("source_origin", "source_head", "reason"),
    (
        ("https://github.com/evil/Lavalamp.git", LAVALAMP_SOURCE_SHA, "SOURCE_ORIGIN_MISMATCH"),
        (LAVALAMP_SOURCE_REPOSITORY, "0" * 40, "SOURCE_SHA_MISMATCH"),
    ),
)
def test_lavalamp_bad_disposable_source_snapshot_blocks_before_overlay_build_or_ota(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source_origin: str,
    source_head: str,
    reason: str,
) -> None:
    artifact_root = tmp_path / "artifacts/lavalamp/issue-1922-c98acbf"
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(maintenance, "LAVALAMP_APPROVED_ARTIFACT_PARENT", tmp_path / "artifacts/lavalamp")
    monkeypatch.setattr(maintenance, "LAVALAMP_ARTIFACT_ROOT", artifact_root)
    fake = FakeRepositoryRunner(source_origin=source_origin, source_head=source_head)
    action = FakeFirmwareAction()
    registered_checkout = tmp_path / "registered-lavalamp"
    registered_checkout.mkdir()

    code, output = RepositoryMaintenanceExecutor(
        project_tree_path=_project_tree(tmp_path, registered_checkout),
        run_command=fake,
        firmware_action=action,
    ).execute(_lavalamp_task(payload__artifact_root=str(artifact_root)))

    assert code == 0
    assert "RESULT: BLOCKED" in output
    assert reason in output
    assert action.executions == 0
    assert not any(call[0][:2] == ["git", "apply"] for call in fake.calls)
    assert not any(call[0] == [sys.executable, "-m", "platformio", "run", "-e", LAVALAMP_PLATFORMIO_ENV] for call in fake.calls)
    assert not any(path.name.startswith("build-") for path in artifact_root.iterdir())


def test_lavalamp_source_snapshot_symlink_blocks_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact_root = tmp_path / "artifacts/lavalamp/issue-1922-c98acbf"
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(maintenance, "LAVALAMP_APPROVED_ARTIFACT_PARENT", tmp_path / "artifacts/lavalamp")
    monkeypatch.setattr(maintenance, "LAVALAMP_ARTIFACT_ROOT", artifact_root)
    action = FakeFirmwareAction()
    calls: list[tuple[list[str], Path | None, int | None, object]] = []

    def fake(args: list[str], cwd: Path | None, timeout: int | None, env: object) -> tuple[int, str]:
        calls.append((args, cwd, timeout, env))
        if args[:2] == ["git", "init"]:
            target = Path(args[2])
            if target.name == "Lavalamp-source":
                outside = tmp_path / "outside-source"
                outside.mkdir()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(outside, target_is_directory=True)
            else:
                target.mkdir(parents=True)
            return 0, ""
        if args[:3] == ["git", "remote", "add"] or args[:2] in (["git", "fetch"], ["git", "checkout"]):
            return 0, ""
        return 1, "unexpected"

    registered_checkout = tmp_path / "registered-lavalamp"
    registered_checkout.mkdir()
    code, output = RepositoryMaintenanceExecutor(
        project_tree_path=_project_tree(tmp_path, registered_checkout),
        run_command=fake,
        firmware_action=action,
    ).execute(_lavalamp_task(payload__artifact_root=str(artifact_root)))

    assert code == 0
    assert "RESULT: BLOCKED" in output
    assert "SOURCE_SNAPSHOT_UNAVAILABLE" in output
    assert action.executions == 0
    assert not any(call[0][:2] == ["git", "apply"] for call in calls)
    assert not any(path.name.startswith("build-") for path in artifact_root.iterdir())


def test_lavalamp_source_snapshot_outside_artifact_root_blocks_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact_root = tmp_path / "artifacts/lavalamp/issue-1922-c98acbf"
    outside_build = tmp_path / "outside-build"
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(maintenance, "LAVALAMP_APPROVED_ARTIFACT_PARENT", tmp_path / "artifacts/lavalamp")
    monkeypatch.setattr(maintenance, "LAVALAMP_ARTIFACT_ROOT", artifact_root)
    monkeypatch.setattr(maintenance.tempfile, "mkdtemp", lambda **_kwargs: str(outside_build))
    fake = FakeRepositoryRunner()
    action = FakeFirmwareAction()
    registered_checkout = tmp_path / "registered-lavalamp"
    registered_checkout.mkdir()

    code, output = RepositoryMaintenanceExecutor(
        project_tree_path=_project_tree(tmp_path, registered_checkout),
        run_command=fake,
        firmware_action=action,
    ).execute(_lavalamp_task(payload__artifact_root=str(artifact_root)))

    assert code == 0
    assert "RESULT: BLOCKED" in output
    assert "SOURCE_SNAPSHOT_OUTSIDE_ARTIFACT_ROOT" in output
    assert action.executions == 0
    assert not outside_build.exists()


def test_lavalamp_verified_completion_does_not_reflash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact_root = tmp_path / "artifacts/lavalamp/issue-1922-c98acbf"
    artifact_root.mkdir(parents=True)
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(maintenance, "LAVALAMP_APPROVED_ARTIFACT_PARENT", tmp_path / "artifacts/lavalamp")
    monkeypatch.setattr(maintenance, "LAVALAMP_ARTIFACT_ROOT", artifact_root)
    firmware = artifact_root / LAVALAMP_FIRMWARE_NAME
    firmware.write_bytes(b"firmware-image")
    digest = "ec4d577ee88cfc72af6589309da85d67feaf32ffabc78e5e705d77c2a5712036"
    manifest = {
        "schema": maintenance.LAVALAMP_EXECUTOR_SCHEMA,
        "status": "DONE",
        "operation": BUILD_AND_LOCAL_OTA_OPERATION,
        "project": "lavalamp",
        "repository": LAVALAMP_REPOSITORY,
        "source_branch": LAVALAMP_SOURCE_BRANCH,
        "source_sha": LAVALAMP_SOURCE_SHA,
        "wled_commit": LAVALAMP_WLED_SHA,
        "environment": LAVALAMP_PLATFORMIO_ENV,
        "artifact_root": str(artifact_root),
        "artifact_files": [LAVALAMP_FIRMWARE_NAME, LAVALAMP_MANIFEST_NAME],
        "relay": "home-edge-01",
        "target": "192.168.1.164",
        "no_direct_controller_lan_ota": True,
        "required_effects": ["CY Anemone", "CY Tidal Bloom"],
        "approval_reference": LAVALAMP_APPROVAL_REFERENCE,
        "idempotency_key": LAVALAMP_IDEMPOTENCY_KEY,
        "byte_size": len(b"firmware-image"),
        "sha256": digest,
        "cleanup_status": "complete",
        "ota": {"final_status": "DONE"},
    }
    (artifact_root / LAVALAMP_MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    checkout = _source_checkout(tmp_path)
    fake = FakeRepositoryRunner()
    action = FakeFirmwareAction()

    _code, output = RepositoryMaintenanceExecutor(
        project_tree_path=_project_tree(tmp_path, checkout),
        run_command=fake,
        firmware_action=action,
    ).execute(_lavalamp_task(payload__artifact_root=str(artifact_root)))

    assert output.startswith("RESULT: DONE")
    assert action.executions == 0
    assert action.postflights == 1
    assert not any(call[0] == [sys.executable, "-m", "platformio", "run", "-e", LAVALAMP_PLATFORMIO_ENV] for call in fake.calls)
