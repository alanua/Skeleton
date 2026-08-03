from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import pytest

from core.runner_repository_maintenance_executor import (
    ARTIFACT_ROOT,
    FIRMWARE_NAME,
    IDEMPOTENCY_KEY,
    MANIFEST_NAME,
    PLATFORMIO_ENV,
    RepositoryMaintenanceExecutor,
    SOURCE_SHA,
    WLED_REPOSITORY,
    WLED_SHA,
)
from core.runner_task import RUNNER_TASK_SCHEMA, RunnerTask


def _task(**updates: object) -> RunnerTask:
    payload = {
        "operation": "build_and_local_ota",
        "project": "lavalamp",
        "repository": "alanua/Lavalamp",
        "source_branch": "main",
        "source_sha": SOURCE_SHA,
        "wled_repository": WLED_REPOSITORY,
        "wled_sha": WLED_SHA,
        "platformio_env": PLATFORMIO_ENV,
        "artifact_root": str(ARTIFACT_ROOT),
        "relay": "home-edge-01",
        "target": "192.168.1.164",
        "idempotency_key": IDEMPOTENCY_KEY,
    }
    mapping = {
        "schema": RUNNER_TASK_SCHEMA,
        "repo": "alanua/Lavalamp",
        "branch": "main",
        "base_sha": SOURCE_SHA,
        "task_kind": "repository_maintenance",
        "payload": payload,
        "requested_capabilities": ["repository_read", "repository_maintenance"],
        "allowed_files": [FIRMWARE_NAME, MANIFEST_NAME],
        "forbidden_actions": ["no_live_build_in_test"],
        "validation_commands": [["python3", "-m", "pytest", "-q"]],
        "validation_timeout_seconds": 900,
        "expected_output": ["artifact receipt"],
        "privacy_boundary": "PUBLIC_SAFE_REPOSITORY_ONLY",
        "approval_reference": "EXPLICIT_APPROVE_LAVALAMP_FIRMWARE_AND_LOCAL_OTA_20260724",
        "idempotency_key": IDEMPOTENCY_KEY,
    }
    for key, value in updates.items():
        if key.startswith("payload__"):
            payload[key.split("__", 1)[1]] = value
        else:
            mapping[key] = value
    return RunnerTask.from_mapping(mapping)


def _source_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "Lavalamp"
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
    return checkout


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


class FakeRunner:
    def __init__(self, checkout: Path) -> None:
        self.checkout = checkout
        self.calls: list[tuple[list[str], Path | None, int | None, Mapping[str, str] | None]] = []

    def __call__(
        self,
        args: list[str],
        cwd: Path | None,
        timeout: int | None,
        env: Mapping[str, str] | None,
    ) -> tuple[int, str]:
        self.calls.append((args, cwd, timeout, env))
        if args[:3] == ["git", "config", "--get"]:
            if cwd and cwd.name == "WLED":
                return 0, WLED_REPOSITORY + "\n"
            return 0, "https://github.com/alanua/Lavalamp.git\n"
        if args[:2] == ["git", "branch"]:
            return 0, "main\n"
        if args[:2] == ["git", "rev-parse"]:
            return 0, (WLED_SHA if cwd and cwd.name == "WLED" else SOURCE_SHA) + "\n"
        if args[:2] == ["git", "status"]:
            return 0, ""
        if args[:2] == ["git", "init"]:
            Path(args[2]).mkdir(parents=True)
            return 0, ""
        if args[:3] == ["git", "remote", "add"]:
            return 0, ""
        if args[:2] == ["git", "fetch"]:
            return 0, ""
        if args[:2] == ["git", "checkout"]:
            return 0, ""
        if args[:2] == ["git", "apply"]:
            assert cwd is not None
            (cwd / "usermods_list.cpp").write_text(
                "cylinder_lava CY Anemone CY Tidal Bloom\n", encoding="utf-8"
            )
            return 0, ""
        if args == ["pio", "--version"]:
            return 0, "PlatformIO\n"
        if args == ["pio", "run", "-e", PLATFORMIO_ENV]:
            assert cwd is not None
            assert env is not None
            assert timeout == 3600
            firmware = cwd / ".pio/build" / PLATFORMIO_ENV / FIRMWARE_NAME
            firmware.parent.mkdir(parents=True)
            firmware.write_bytes(b"firmware-image")
            return 0, ""
        return 1, "unexpected"


def test_mismatched_packet_blocks_before_commands(tmp_path: Path) -> None:
    fake_action = FakeFirmwareAction()
    calls: list[list[str]] = []
    executor = RepositoryMaintenanceExecutor(
        project_tree_path=tmp_path / "missing.yaml",
        run_command=lambda args, cwd, timeout, env: (calls.append(args) or (0, "")),
        firmware_action=fake_action,
    )

    code, output = executor.execute(_task(payload__source_sha="0" * 40))

    assert code == 0
    assert "RESULT: BLOCKED" in output
    assert "SOURCE_SHA_MISMATCH" in output
    assert calls == []
    assert fake_action.executions == 0


def test_wrong_approval_blocks_before_build_and_ota(tmp_path: Path) -> None:
    fake_action = FakeFirmwareAction()
    executor = RepositoryMaintenanceExecutor(
        project_tree_path=tmp_path / "missing.yaml",
        run_command=lambda args, cwd, timeout, env: (1, "should not run"),
        firmware_action=fake_action,
    )

    _code, output = executor.execute(_task(approval_reference="WRONG_APPROVAL"))

    assert "RESULT: BLOCKED" in output
    assert "APPROVAL_REFERENCE_MISMATCH" in output
    assert fake_action.executions == 0


def test_exact_build_publishes_only_artifact_and_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts/lavalamp/source-issue-2"
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr("core.runner_repository_maintenance_executor.APPROVED_ARTIFACT_PARENT", tmp_path / "artifacts/lavalamp")
    monkeypatch.setattr("core.runner_repository_maintenance_executor.ARTIFACT_ROOT", artifact_root)
    task = _task(payload__artifact_root=str(artifact_root))
    checkout = _source_checkout(tmp_path)
    fake = FakeRunner(checkout)
    action = FakeFirmwareAction()

    code, output = RepositoryMaintenanceExecutor(
        project_tree_path=_project_tree(tmp_path, checkout),
        run_command=fake,
        firmware_action=action,
    ).execute(task)

    assert code == 0
    assert output.startswith("RESULT: DONE")
    assert sorted(path.name for path in artifact_root.iterdir()) == [FIRMWARE_NAME, MANIFEST_NAME]
    manifest = json.loads((artifact_root / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["source_sha"] == SOURCE_SHA
    assert manifest["wled_sha"] == WLED_SHA
    assert manifest["build_command"] == f"pio run -e {PLATFORMIO_ENV}"
    assert manifest["cleanup_status"] == "complete"
    assert action.executions == 1
    assert any(call[0] == ["git", "fetch", "--depth", "1", "origin", WLED_SHA] for call in fake.calls)
    assert any(call[0] == ["pio", "run", "-e", PLATFORMIO_ENV] for call in fake.calls)


def test_verified_completed_manifest_reuses_artifact_without_reflash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts/lavalamp/source-issue-2"
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    artifact_root.mkdir(parents=True)
    monkeypatch.setattr("core.runner_repository_maintenance_executor.APPROVED_ARTIFACT_PARENT", tmp_path / "artifacts/lavalamp")
    monkeypatch.setattr("core.runner_repository_maintenance_executor.ARTIFACT_ROOT", artifact_root)
    firmware = artifact_root / FIRMWARE_NAME
    firmware.write_bytes(b"firmware-image")
    digest = "ec4d577ee88cfc72af6589309da85d67feaf32ffabc78e5e705d77c2a5712036"
    (artifact_root / MANIFEST_NAME).write_text(
        json.dumps(
            {
                "schema": "skeleton.repository_maintenance.lavalamp_build_ota.v1",
                "status": "DONE",
                "operation": "build_and_local_ota",
                "project": "lavalamp",
                "repository": "alanua/Lavalamp",
                "source_branch": "main",
                "source_sha": SOURCE_SHA,
                "wled_repository": WLED_REPOSITORY,
                "wled_sha": WLED_SHA,
                "platformio_env": PLATFORMIO_ENV,
                "artifact_root": str(artifact_root),
                "artifact_files": [FIRMWARE_NAME, MANIFEST_NAME],
                "relay": "home-edge-01",
                "target": "192.168.1.164",
                "no_direct_controller_lan_ota": True,
                "postflight_effects": ["CY Anemone", "CY Tidal Bloom"],
                "idempotency_key": IDEMPOTENCY_KEY,
                "byte_size": len(b"firmware-image"),
                "sha256": digest,
                "cleanup_status": "complete",
                "ota": {"final_status": "DONE"},
            }
        ),
        encoding="utf-8",
    )
    checkout = _source_checkout(tmp_path)
    action = FakeFirmwareAction()
    fake = FakeRunner(checkout)

    _code, output = RepositoryMaintenanceExecutor(
        project_tree_path=_project_tree(tmp_path, checkout),
        run_command=fake,
        firmware_action=action,
    ).execute(_task(payload__artifact_root=str(artifact_root)))

    assert output.startswith("RESULT: DONE")
    assert action.executions == 0
    assert action.postflights == 1
    assert not any(call[0] == ["pio", "run", "-e", PLATFORMIO_ENV] for call in fake.calls)


def test_corrupt_manifest_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts/lavalamp/source-issue-2"
    monkeypatch.setenv("RUNNER_APPROVED_WORKSPACE_ROOT", str(tmp_path))
    artifact_root.mkdir(parents=True)
    monkeypatch.setattr("core.runner_repository_maintenance_executor.APPROVED_ARTIFACT_PARENT", tmp_path / "artifacts/lavalamp")
    monkeypatch.setattr("core.runner_repository_maintenance_executor.ARTIFACT_ROOT", artifact_root)
    (artifact_root / FIRMWARE_NAME).write_bytes(b"firmware-image")
    (artifact_root / MANIFEST_NAME).write_text("{bad json", encoding="utf-8")
    checkout = _source_checkout(tmp_path)

    _code, output = RepositoryMaintenanceExecutor(
        project_tree_path=_project_tree(tmp_path, checkout),
        run_command=FakeRunner(checkout),
        firmware_action=FakeFirmwareAction(),
    ).execute(_task(payload__artifact_root=str(artifact_root)))

    assert "RESULT: BLOCKED" in output
    assert "STALE_ARTIFACT_MANIFEST" in output
