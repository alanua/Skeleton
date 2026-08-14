from __future__ import annotations

import json
from pathlib import Path
import subprocess

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
        if args == ["pio", "--version"]:
            return 0, "PlatformIO\n"
        if args == ["pio", "run", "-e", LAVALAMP_PLATFORMIO_ENV]:
            assert cwd is not None
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
    assert any(call[0] == ["pio", "run", "-e", LAVALAMP_PLATFORMIO_ENV] for call in fake.calls)
    assert not any(path.name.startswith("build-") for path in artifact_root.iterdir())


def test_lavalamp_shared_checkout_head_is_not_historical_source_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact_root = tmp_path / "artifacts/lavalamp/issue-1922-c98acbf"
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(maintenance, "LAVALAMP_APPROVED_ARTIFACT_PARENT", tmp_path / "artifacts/lavalamp")
    monkeypatch.setattr(maintenance, "LAVALAMP_ARTIFACT_ROOT", artifact_root)
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
    assert not any(call[0] == ["pio", "run", "-e", LAVALAMP_PLATFORMIO_ENV] for call in fake.calls)
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
    assert not any(call[0] == ["pio", "run", "-e", LAVALAMP_PLATFORMIO_ENV] for call in fake.calls)
