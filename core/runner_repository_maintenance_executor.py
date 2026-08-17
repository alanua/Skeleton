from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import Any, Final
import urllib.error
import urllib.request

from core.codex_runtime_recovery import (
    CodexRuntimeRecoveryError,
    TARGET_CODEX_MODEL,
    TARGET_CODEX_VERSION,
    pinned_codex_runtime_path,
    recover_pinned_codex_runtime,
)
from core.home_edge.firmware_action import (
    FirmwareTransferRequest,
    HomeEdgeFirmwareAction,
    HomeEdgeFirmwareActionError,
)
from core.project_tree import get_project, load_project_tree
from core.runner_executor import RunnerExecutorError, validate_executor_task
from core.runner_task import RunnerTask


REGISTERED_ACTION_CHECK_SKELETON_FRESHNESS = "check_skeleton_freshness"
REGISTERED_ACTION_RECOVER_SKELETON_CHECKOUT = "recover_skeleton_checkout"
REGISTERED_ACTION_REPLENISH_RUNNER_QUEUE = "replenish_runner_queue"

BUILD_AND_LOCAL_OTA_OPERATION: Final = "build_and_local_ota"
LAVALAMP_PROJECT: Final = "lavalamp"
LAVALAMP_REPOSITORY: Final = "alanua/Lavalamp"
LAVALAMP_SOURCE_REPOSITORY: Final = "https://github.com/alanua/Lavalamp.git"
LAVALAMP_SOURCE_BRANCH: Final = "main"
LAVALAMP_SOURCE_SHA: Final = "c98acbf12c51492bee32e1ab07dd349752e4bee5"
LAVALAMP_WLED_REPOSITORY: Final = "https://github.com/wled/WLED.git"
LAVALAMP_WLED_SHA: Final = "58a84b653672b3611bc90cbf1b52bd1615132468"
LAVALAMP_PLATFORMIO_ENV: Final = "cylinder_lava_esp32"
LAVALAMP_ARTIFACT_ROOT: Final = Path(
    "/home/agent/agent-dev/artifacts/lavalamp/issue-1922-c98acbf"
)
LAVALAMP_APPROVED_ARTIFACT_PARENT: Final = Path(
    "/home/agent/agent-dev/artifacts/lavalamp"
)
LAVALAMP_RELAY: Final = "home-edge-01"
LAVALAMP_TARGET: Final = "192.168.1.164"
LAVALAMP_APPROVAL_REFERENCE: Final = (
    "EXPLICIT_BUILD_AND_LOCAL_OTA_LAVALAMP_20260812_1810"
)
LAVALAMP_IDEMPOTENCY_KEY: Final = (
    "lavalamp-c98acbf-build-ota-1922-20260812-v2"
)
LAVALAMP_REQUIRED_EFFECTS: Final = ("CY Anemone", "CY Tidal Bloom")
LAVALAMP_FIRMWARE_NAME: Final = "firmware.bin"
LAVALAMP_MANIFEST_NAME: Final = "manifest.json"
LAVALAMP_ALLOWED_ARTIFACTS: Final = (LAVALAMP_FIRMWARE_NAME, LAVALAMP_MANIFEST_NAME)
LAVALAMP_MAX_FIRMWARE_BYTES: Final = 4 * 1024 * 1024
LAVALAMP_BUILD_TIMEOUT_SECONDS: Final = 3600
LAVALAMP_EXECUTOR_SCHEMA: Final = (
    "skeleton.repository_maintenance.lavalamp_build_ota.v1"
)
LAVALAMP_PLATFORMIO_VERSION: Final = "6.1.19"
LAVALAMP_NODE_URL: Final = (
    "https://nodejs.org/dist/v20.20.2/node-v20.20.2-linux-x64.tar.xz"
)
LAVALAMP_NODE_SHA256: Final = (
    "df770b2a6f130ed8627c9782c988fda9669fa23898329a61a871e32f965e007d"
)
LAVALAMP_NODE_ARCHIVE_MAX_BYTES: Final = 64 * 1024 * 1024
LAVALAMP_TOOL_BOOTSTRAP_TIMEOUT_SECONDS: Final = 300
LAVALAMP_NODE_DOWNLOAD_TIMEOUT_SECONDS: Final = 60
_LAVALAMP_ALLOWED_CAPABILITY_SETS: Final = frozenset(
    {
        frozenset(("repository_read", "test_execution")),
        frozenset(("repository_read", "repository_maintenance", "test_execution")),
        frozenset(("repository_read", "repository_maintenance", "subprocess_isolated", "test_execution")),
    }
)

class RepositoryMaintenanceBlocked(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


RepositoryRunCommand = Callable[
    [list[str], Path | None, int | None, Mapping[str, str] | None],
    tuple[int, str],
]


def _repository_run_command(
    args: list[str],
    cwd: Path | None,
    timeout: int | None,
    env: Mapping[str, str] | None,
) -> tuple[int, str]:
    merged_env = _safe_child_environment()
    if env:
        merged_env.update(env)
    completed = subprocess.run(
        args,
        cwd=str(cwd) if cwd is not None else None,
        env=merged_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    return completed.returncode, "\n".join(
        part for part in (completed.stdout, completed.stderr) if part
    )

RUNNER_SERVICE = "skeleton-runner-poll.service"
RUNNER_TIMER = "skeleton-runner-poll.timer"
_SUDO_BIN = "/usr/bin/sudo"
_SYSTEMCTL_BIN = "/usr/bin/systemctl"
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
    # The canonical production Runner is the fixed system-level oneshot timer.
    # Use only absolute code-owned binaries and unit names. Never try user scope
    # or an alternate fallback authority.
    for argv in (
        [_SUDO_BIN, "-n", _SYSTEMCTL_BIN, "daemon-reload"],
        [_SUDO_BIN, "-n", _SYSTEMCTL_BIN, "reset-failed", RUNNER_SERVICE],
        [_SUDO_BIN, "-n", _SYSTEMCTL_BIN, "reset-failed", RUNNER_TIMER],
        [_SUDO_BIN, "-n", _SYSTEMCTL_BIN, "restart", RUNNER_TIMER],
        [_SUDO_BIN, "-n", _SYSTEMCTL_BIN, "is-enabled", "--quiet", RUNNER_TIMER],
        [_SUDO_BIN, "-n", _SYSTEMCTL_BIN, "is-active", "--quiet", RUNNER_TIMER],
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
class RepositoryMaintenanceExecutor:
    project_tree_path: Path = Path(__file__).resolve().parents[1] / "PROJECT_TREE.yaml"
    run_command: RepositoryRunCommand = _repository_run_command
    firmware_action: HomeEdgeFirmwareAction | None = None
    task_kind: str = "repository_maintenance"
    required_capabilities: tuple[str, ...] = ("repository_maintenance", "repository_read")

    def execute(self, task: RunnerTask) -> tuple[int, str]:
        try:
            validate_executor_task(self.task_kind, task)
            self._validate_lavalamp_packet(task)
            self._verify_lavalamp_registry()
            artifact_root = self._validated_artifact_root()
            manifest_path = artifact_root / LAVALAMP_MANIFEST_NAME
            firmware_path = artifact_root / LAVALAMP_FIRMWARE_NAME
            manifest = self._verified_existing_manifest(manifest_path, firmware_path)
            if manifest is None:
                manifest = self._build_lavalamp_artifact(artifact_root)

            request = FirmwareTransferRequest(
                firmware_path=firmware_path,
                byte_size=int(manifest["byte_size"]),
                sha256=str(manifest["sha256"]),
                relay=LAVALAMP_RELAY,
                target=LAVALAMP_TARGET,
                postflight_effects=LAVALAMP_REQUIRED_EFFECTS,
                idempotency_key=LAVALAMP_IDEMPOTENCY_KEY,
            )
            action = self.firmware_action or HomeEdgeFirmwareAction()
            duplicate = manifest.get("status") == "DONE" and isinstance(manifest.get("ota"), Mapping)
            if duplicate:
                ota_receipt = action.verify_postflight_only(request)
            else:
                ota_receipt = action.execute(request)
                manifest = {
                    **manifest,
                    "status": "DONE",
                    "updated_at": _utc_now(),
                    "ota": ota_receipt,
                }
                _atomic_write_json(manifest_path, manifest)
            return 0, _repository_result("DONE", _public_receipt(manifest, ota_receipt, duplicate))
        except RepositoryMaintenanceBlocked as exc:
            return 0, _repository_result("BLOCKED", _blocked_receipt(exc.reason_code))
        except HomeEdgeFirmwareActionError as exc:
            return 0, _repository_result("BLOCKED", _blocked_receipt(exc.reason_code))
        except RunnerExecutorError:
            raise
        except Exception:
            return 0, _repository_result("BLOCKED", _blocked_receipt("REPOSITORY_MAINTENANCE_FAILED"))

    def _validate_lavalamp_packet(self, task: RunnerTask) -> None:
        payload = task.payload
        expected_payload = {
            "operation": BUILD_AND_LOCAL_OTA_OPERATION,
            "project": LAVALAMP_PROJECT,
            "repository": LAVALAMP_REPOSITORY,
            "source_branch": LAVALAMP_SOURCE_BRANCH,
            "source_sha": LAVALAMP_SOURCE_SHA,
            "wled_commit": LAVALAMP_WLED_SHA,
            "environment": LAVALAMP_PLATFORMIO_ENV,
            "artifact_root": str(LAVALAMP_ARTIFACT_ROOT),
            "relay": LAVALAMP_RELAY,
            "target": LAVALAMP_TARGET,
            "approval_reference": LAVALAMP_APPROVAL_REFERENCE,
            "idempotency_key": LAVALAMP_IDEMPOTENCY_KEY,
            "required_effects": LAVALAMP_REQUIRED_EFFECTS,
        }
        if set(payload) != set(expected_payload):
            raise RepositoryMaintenanceBlocked("PACKET_FIELD_SET_MISMATCH")
        for key, expected in expected_payload.items():
            if payload.get(key) != expected:
                raise RepositoryMaintenanceBlocked(f"{key.upper()}_MISMATCH")
        if task.repo != LAVALAMP_REPOSITORY:
            raise RepositoryMaintenanceBlocked("TASK_REPOSITORY_MISMATCH")
        if task.branch != LAVALAMP_SOURCE_BRANCH or task.base_sha != LAVALAMP_SOURCE_SHA:
            raise RepositoryMaintenanceBlocked("TASK_SOURCE_REF_MISMATCH")
        if task.approval_reference != LAVALAMP_APPROVAL_REFERENCE:
            raise RepositoryMaintenanceBlocked("APPROVAL_REFERENCE_MISMATCH")
        if task.idempotency_key != LAVALAMP_IDEMPOTENCY_KEY:
            raise RepositoryMaintenanceBlocked("TASK_IDEMPOTENCY_KEY_MISMATCH")
        if frozenset(task.requested_capabilities) not in _LAVALAMP_ALLOWED_CAPABILITY_SETS:
            raise RepositoryMaintenanceBlocked("CAPABILITY_SET_MISMATCH")
        if tuple(task.allowed_files) != LAVALAMP_ALLOWED_ARTIFACTS:
            raise RepositoryMaintenanceBlocked("ARTIFACT_ALLOWLIST_MISMATCH")
        if task.privacy_boundary != "PUBLIC_SAFE_REPOSITORY_ONLY":
            raise RepositoryMaintenanceBlocked("PRIVACY_BOUNDARY_MISMATCH")

    def _verify_lavalamp_registry(self) -> None:
        try:
            project = get_project(load_project_tree(self.project_tree_path), LAVALAMP_PROJECT)
        except Exception as exc:
            raise RepositoryMaintenanceBlocked("PROJECT_REGISTRY_UNAVAILABLE") from exc
        if project.get("repo") != LAVALAMP_REPOSITORY:
            raise RepositoryMaintenanceBlocked("PROJECT_REGISTRY_REPOSITORY_MISMATCH")

    def _validated_artifact_root(self) -> Path:
        root = LAVALAMP_ARTIFACT_ROOT
        parent = LAVALAMP_APPROVED_ARTIFACT_PARENT
        if root.is_symlink() or parent.is_symlink():
            raise RepositoryMaintenanceBlocked("ARTIFACT_ROOT_SYMLINK")
        resolved_parent = parent.resolve()
        resolved_root = root.resolve()
        if resolved_root == resolved_parent:
            raise RepositoryMaintenanceBlocked("ARTIFACT_ROOT_OUTSIDE_ALLOWLIST")
        try:
            resolved_root.relative_to(resolved_parent)
        except ValueError as exc:
            raise RepositoryMaintenanceBlocked("ARTIFACT_ROOT_OUTSIDE_ALLOWLIST") from exc
        resolved_root.mkdir(parents=True, exist_ok=True)
        if resolved_root.is_symlink():
            raise RepositoryMaintenanceBlocked("ARTIFACT_ROOT_SYMLINK")
        _assert_not_repository_path(resolved_root)
        return resolved_root

    def _verified_existing_manifest(self, manifest_path: Path, firmware_path: Path) -> dict[str, object] | None:
        if not manifest_path.exists() and not firmware_path.exists():
            return None
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise RepositoryMaintenanceBlocked("STALE_ARTIFACT_MANIFEST")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RepositoryMaintenanceBlocked("STALE_ARTIFACT_MANIFEST") from exc
        if not isinstance(manifest, dict) or _manifest_mismatch_reason(manifest):
            raise RepositoryMaintenanceBlocked("STALE_ARTIFACT_MANIFEST")
        self._verify_firmware_file(firmware_path, str(manifest["sha256"]), int(manifest["byte_size"]))
        _assert_only_final_artifacts(manifest_path.parent)
        return manifest

    def _build_lavalamp_artifact(self, artifact_root: Path) -> dict[str, object]:
        temp_dir: Path | None = None
        try:
            temp_dir = Path(tempfile.mkdtemp(prefix="build-", dir=str(artifact_root))).resolve()
            source_checkout = temp_dir / "Lavalamp-source"
            _ok(self.run_command(["git", "init", str(source_checkout)], None, 30, None), "SOURCE_INIT_FAILED")
            _ok(
                self.run_command(["git", "remote", "add", "origin", LAVALAMP_SOURCE_REPOSITORY], source_checkout, 30, None),
                "SOURCE_REMOTE_FAILED",
            )
            _ok(
                self.run_command(["git", "fetch", "--depth", "1", "origin", LAVALAMP_SOURCE_SHA], source_checkout, 600, None),
                "SOURCE_FETCH_FAILED",
            )
            _ok(self.run_command(["git", "checkout", "--detach", LAVALAMP_SOURCE_SHA], source_checkout, 30, None), "SOURCE_CHECKOUT_FAILED")
            self._verify_lavalamp_source_snapshot(source_checkout, artifact_root)

            wled_dir = temp_dir / "WLED"
            _ok(self.run_command(["git", "init", str(wled_dir)], None, 30, None), "WLED_INIT_FAILED")
            _ok(self.run_command(["git", "remote", "add", "origin", LAVALAMP_WLED_REPOSITORY], wled_dir, 30, None), "WLED_REMOTE_FAILED")
            _ok(self.run_command(["git", "fetch", "--depth", "1", "origin", LAVALAMP_WLED_SHA], wled_dir, 600, None), "WLED_FETCH_FAILED")
            _ok(self.run_command(["git", "checkout", "--detach", LAVALAMP_WLED_SHA], wled_dir, 30, None), "WLED_CHECKOUT_FAILED")
            if _git(self.run_command, ["rev-parse", "HEAD"], wled_dir) != LAVALAMP_WLED_SHA:
                raise RepositoryMaintenanceBlocked("WLED_HEAD_MISMATCH")

            self._verify_lavalamp_source_snapshot(source_checkout, artifact_root)
            self._apply_lavalamp_overlay(source_checkout, wled_dir)
            self._verify_effect_registration(wled_dir)
            tools_dir = temp_dir / "tools"
            node_bin = _bootstrap_node_runtime(temp_dir, self.run_command)
            platformio_target = tools_dir / "platformio"
            platformio_env = _bootstrap_platformio(self.run_command, temp_dir, platformio_target)
            build_env = {
                **platformio_env,
                "PATH": f"{node_bin}{os.pathsep}{os.environ.get('PATH', '')}",
                "HOME": str(temp_dir / "home"),
                "NPM_CONFIG_CACHE": str(temp_dir / "npm-cache"),
                "NPM_CONFIG_USERCONFIG": str(temp_dir / "npmrc"),
                "NPM_CONFIG_GLOBALCONFIG": str(temp_dir / "npm-globalrc"),
                "NPM_CONFIG_PREFIX": str(temp_dir / "npm-prefix"),
                "PLATFORMIO_CORE_DIR": str(temp_dir / "pio-core"),
                "PLATFORMIO_BUILD_CACHE_DIR": str(temp_dir / "pio-build-cache"),
                "PLATFORMIO_GLOBALLIB_DIR": str(temp_dir / "pio-lib"),
                "PLATFORMIO_PACKAGES_DIR": str(temp_dir / "pio-packages"),
            }
            command = [sys.executable, "-m", "platformio"]
            env = {
                key: value
                for key, value in build_env.items()
                if key in {
                    "PATH",
                    "HOME",
                    "PYTHONPATH",
                    "PYTHONNOUSERSITE",
                    "NPM_CONFIG_CACHE",
                    "NPM_CONFIG_USERCONFIG",
                    "NPM_CONFIG_GLOBALCONFIG",
                    "NPM_CONFIG_PREFIX",
                    "PLATFORMIO_CORE_DIR",
                    "PLATFORMIO_BUILD_CACHE_DIR",
                    "PLATFORMIO_GLOBALLIB_DIR",
                    "PLATFORMIO_PACKAGES_DIR",
                }
            }
            _ok_run(
                self.run_command,
                [*command, "run", "-e", LAVALAMP_PLATFORMIO_ENV],
                wled_dir,
                LAVALAMP_BUILD_TIMEOUT_SECONDS,
                env,
                "PLATFORMIO_BUILD_FAILED",
                "PLATFORMIO_RUNTIME_UNAVAILABLE",
            )
            built = wled_dir / ".pio" / "build" / LAVALAMP_PLATFORMIO_ENV / LAVALAMP_FIRMWARE_NAME
            size, digest = self._verify_built_firmware(built)
            firmware_dest = artifact_root / LAVALAMP_FIRMWARE_NAME
            manifest_dest = artifact_root / LAVALAMP_MANIFEST_NAME
            _atomic_copy(built, firmware_dest)
            now = _utc_now()
            manifest: dict[str, object] = {
                "schema": LAVALAMP_EXECUTOR_SCHEMA,
                "status": "BUILT",
                "operation": BUILD_AND_LOCAL_OTA_OPERATION,
                "project": LAVALAMP_PROJECT,
                "repository": LAVALAMP_REPOSITORY,
                "source_branch": LAVALAMP_SOURCE_BRANCH,
                "source_sha": LAVALAMP_SOURCE_SHA,
                "wled_commit": LAVALAMP_WLED_SHA,
                "environment": LAVALAMP_PLATFORMIO_ENV,
                "artifact_root": str(LAVALAMP_ARTIFACT_ROOT),
                "artifact_files": list(LAVALAMP_ALLOWED_ARTIFACTS),
                "firmware_path": str(firmware_dest),
                "byte_size": size,
                "sha256": digest,
                "build_command": " ".join([*command, "run", "-e", LAVALAMP_PLATFORMIO_ENV]),
                "build_timeout_seconds": LAVALAMP_BUILD_TIMEOUT_SECONDS,
                "relay": LAVALAMP_RELAY,
                "target": LAVALAMP_TARGET,
                "no_direct_controller_lan_ota": True,
                "required_effects": list(LAVALAMP_REQUIRED_EFFECTS),
                "approval_reference": LAVALAMP_APPROVAL_REFERENCE,
                "idempotency_key": LAVALAMP_IDEMPOTENCY_KEY,
                "public_status": "artifact_built_pending_home_edge_ota",
                "created_at": now,
                "updated_at": now,
                "cleanup_status": "pending",
            }
            _atomic_write_json(manifest_dest, manifest)
            shutil.rmtree(temp_dir, ignore_errors=True)
            temp_dir = None
            _assert_only_final_artifacts(artifact_root)
            manifest["cleanup_status"] = "complete"
            _atomic_write_json(manifest_dest, manifest)
            return manifest
        finally:
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _verify_lavalamp_source_snapshot(self, source_checkout: Path, artifact_root: Path) -> None:
        if source_checkout.is_symlink() or not source_checkout.is_dir():
            raise RepositoryMaintenanceBlocked("SOURCE_SNAPSHOT_UNAVAILABLE")
        resolved_root = artifact_root.resolve()
        resolved_source = source_checkout.resolve()
        try:
            resolved_source.relative_to(resolved_root)
        except ValueError as exc:
            raise RepositoryMaintenanceBlocked("SOURCE_SNAPSHOT_OUTSIDE_ARTIFACT_ROOT") from exc
        if _git(self.run_command, ["config", "--get", "remote.origin.url"], resolved_source) != LAVALAMP_SOURCE_REPOSITORY:
            raise RepositoryMaintenanceBlocked("SOURCE_ORIGIN_MISMATCH")
        if _git(self.run_command, ["branch", "--show-current"], resolved_source):
            raise RepositoryMaintenanceBlocked("SOURCE_BRANCH_MISMATCH")
        if _git(self.run_command, ["rev-parse", "HEAD"], resolved_source) != LAVALAMP_SOURCE_SHA:
            raise RepositoryMaintenanceBlocked("SOURCE_SHA_MISMATCH")
        if _git(self.run_command, ["status", "--porcelain=v1", "--untracked-files=all"], resolved_source):
            raise RepositoryMaintenanceBlocked("SOURCE_CHECKOUT_DIRTY")

    def _apply_lavalamp_overlay(self, source_checkout: Path, wled_dir: Path) -> None:
        override = source_checkout / "overlays" / "wled" / "platformio_override.ini"
        usermod = source_checkout / "overlays" / "wled" / "usermods" / "cylinder_lava"
        patch = source_checkout / "patches" / "wled-usermods-list-cylinder-lava.patch"
        if not override.is_file() or override.is_symlink():
            raise RepositoryMaintenanceBlocked("LAVALAMP_OVERLAY_MISSING")
        if not usermod.is_dir() or usermod.is_symlink():
            raise RepositoryMaintenanceBlocked("LAVALAMP_USERMOD_MISSING")
        if any(path.is_symlink() for path in usermod.rglob("*")):
            raise RepositoryMaintenanceBlocked("LAVALAMP_USERMOD_SYMLINK")
        if not patch.is_file() or patch.is_symlink():
            raise RepositoryMaintenanceBlocked("LAVALAMP_PATCH_MISSING")
        shutil.copy2(override, wled_dir / "platformio_override.ini")
        shutil.copytree(usermod, wled_dir / "usermods" / "cylinder_lava", symlinks=False)
        code, _output = self.run_command(["git", "apply", "--whitespace=nowarn", str(patch)], wled_dir, 30, None)
        if code != 0:
            raise RepositoryMaintenanceBlocked("LAVALAMP_PATCH_APPLY_FAILED")

    def _verify_effect_registration(self, wled_dir: Path) -> None:
        haystack = []
        for relative in (
            Path("usermods/cylinder_lava"),
            Path("usermods_list.cpp"),
            Path("wled00/usermods_list.cpp"),
            Path("platformio_override.ini"),
        ):
            path = wled_dir / relative
            if path.is_file() and not path.is_symlink():
                haystack.append(path.read_text(encoding="utf-8", errors="ignore"))
            elif path.is_dir() and not path.is_symlink():
                for child in path.rglob("*"):
                    if child.is_file() and not child.is_symlink():
                        haystack.append(child.read_text(encoding="utf-8", errors="ignore"))
        text = "\n".join(haystack)
        if "cylinder_lava" not in text or not all(effect in text for effect in LAVALAMP_REQUIRED_EFFECTS):
            raise RepositoryMaintenanceBlocked("LAVALAMP_EFFECT_REGISTRATION_MISSING")

    def _verify_built_firmware(self, path: Path) -> tuple[int, str]:
        if not path.is_file() or path.is_symlink():
            raise RepositoryMaintenanceBlocked("FIRMWARE_OUTPUT_MISSING")
        size = path.stat().st_size
        if size <= 0:
            raise RepositoryMaintenanceBlocked("FIRMWARE_OUTPUT_EMPTY")
        if size > LAVALAMP_MAX_FIRMWARE_BYTES:
            raise RepositoryMaintenanceBlocked("FIRMWARE_OUTPUT_OVERSIZED")
        return size, hashlib.sha256(path.read_bytes()).hexdigest()

    def _verify_firmware_file(self, path: Path, digest: str, size: int) -> None:
        actual_size, actual_digest = self._verify_built_firmware(path)
        if actual_size != size or actual_digest != digest:
            raise RepositoryMaintenanceBlocked("FIRMWARE_ARTIFACT_MISMATCH")


def _git(run_command: RepositoryRunCommand, args: list[str], cwd: Path) -> str:
    code, output = run_command(["git", *args], cwd, 30, None)
    if code != 0:
        raise RepositoryMaintenanceBlocked("GIT_COMMAND_FAILED")
    return output.strip()


def _ok(result: tuple[int, str], reason: str) -> None:
    code, _output = result
    if code != 0:
        raise RepositoryMaintenanceBlocked(reason)


def _ok_run(
    run_command: RepositoryRunCommand,
    args: list[str],
    cwd: Path | None,
    timeout: int | None,
    env: Mapping[str, str] | None,
    failure_reason: str,
    missing_reason: str,
) -> None:
    try:
        result = run_command(args, cwd, timeout, env)
    except FileNotFoundError as exc:
        raise RepositoryMaintenanceBlocked(missing_reason) from exc
    except OSError as exc:
        raise RepositoryMaintenanceBlocked(missing_reason) from exc
    _ok(result, failure_reason)


def _bootstrap_platformio(
    run_command: RepositoryRunCommand,
    temp_dir: Path,
    target_dir: Path,
) -> dict[str, str]:
    cache_dir = temp_dir / "pip-cache"
    target_dir.mkdir(parents=True, exist_ok=True)
    env = {
        "PYTHONNOUSERSITE": "1",
        "PIP_CACHE_DIR": str(cache_dir),
        "PIP_TARGET": str(target_dir),
        "HOME": str(temp_dir / "home"),
    }
    _ok_run(
        run_command,
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--target",
            str(target_dir),
            f"platformio=={LAVALAMP_PLATFORMIO_VERSION}",
        ],
        None,
        LAVALAMP_TOOL_BOOTSTRAP_TIMEOUT_SECONDS,
        env,
        "PLATFORMIO_BOOTSTRAP_FAILED",
        "PYTHON_PIP_UNAVAILABLE",
    )
    return {
        "PYTHONPATH": str(target_dir),
        "PYTHONNOUSERSITE": "1",
    }


def _bootstrap_node_runtime(temp_dir: Path, run_command: RepositoryRunCommand) -> Path:
    if platform.system() != "Linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise RepositoryMaintenanceBlocked("NODE_UNSUPPORTED_HOST")

    archive = _download_verified_node_archive(temp_dir)
    extract_root = temp_dir / "tools" / "node"
    _safe_extract_tar_xz(archive, extract_root)
    node_home = extract_root / "node-v20.20.2-linux-x64"
    node_bin = node_home / "bin"
    env = {
        "PATH": f"{node_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(temp_dir / "home"),
        "NPM_CONFIG_CACHE": str(temp_dir / "npm-cache"),
        "NPM_CONFIG_USERCONFIG": str(temp_dir / "npmrc"),
        "NPM_CONFIG_GLOBALCONFIG": str(temp_dir / "npm-globalrc"),
        "NPM_CONFIG_PREFIX": str(temp_dir / "npm-prefix"),
    }
    _ok_run(
        run_command,
        [str(node_bin / "node"), "--version"],
        None,
        30,
        env,
        "NODE_RUNTIME_FAILED",
        "NODE_RUNTIME_UNAVAILABLE",
    )
    _ok_run(
        run_command,
        [str(node_bin / "npm"), "--version"],
        None,
        30,
        env,
        "NODE_RUNTIME_FAILED",
        "NODE_RUNTIME_UNAVAILABLE",
    )
    return node_bin


def _download_verified_node_archive(temp_dir: Path) -> Path:
    archive_path = temp_dir / "tools" / "downloads" / "node.tar.xz"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    try:
        with urllib.request.urlopen(
            LAVALAMP_NODE_URL,
            timeout=LAVALAMP_NODE_DOWNLOAD_TIMEOUT_SECONDS,
        ) as response:
            with archive_path.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > LAVALAMP_NODE_ARCHIVE_MAX_BYTES:
                        raise RepositoryMaintenanceBlocked("NODE_DOWNLOAD_TOO_LARGE")
                    digest.update(chunk)
                    output.write(chunk)
    except RepositoryMaintenanceBlocked:
        raise
    except (OSError, urllib.error.URLError) as exc:
        raise RepositoryMaintenanceBlocked("NODE_DOWNLOAD_FAILED") from exc
    if digest.hexdigest() != LAVALAMP_NODE_SHA256:
        raise RepositoryMaintenanceBlocked("NODE_CHECKSUM_MISMATCH")
    return archive_path


def _safe_extract_tar_xz(archive_path: Path, extract_root: Path) -> None:
    extract_root.mkdir(parents=True, exist_ok=True)
    resolved_root = extract_root.resolve()
    try:
        with tarfile.open(archive_path, mode="r:xz") as archive:
            for member in archive.getmembers():
                target = (extract_root / member.name).resolve()
                try:
                    target.relative_to(resolved_root)
                except ValueError as exc:
                    raise RepositoryMaintenanceBlocked("NODE_EXTRACTION_BLOCKED") from exc
                if member.issym() or member.islnk():
                    link_target = (target.parent / member.linkname).resolve()
                    try:
                        link_target.relative_to(resolved_root)
                    except ValueError as exc:
                        raise RepositoryMaintenanceBlocked("NODE_EXTRACTION_BLOCKED") from exc
            try:
                archive.extractall(extract_root, filter="fully_trusted")
            except TypeError:
                archive.extractall(extract_root)
    except RepositoryMaintenanceBlocked:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise RepositoryMaintenanceBlocked("NODE_EXTRACTION_FAILED") from exc


def _assert_not_repository_path(path: Path) -> None:
    current = path.resolve()
    approved_parent = LAVALAMP_APPROVED_ARTIFACT_PARENT.resolve()
    candidates = [current]
    for candidate in current.parents:
        candidates.append(candidate)
        if candidate == approved_parent:
            break
    for candidate in candidates:
        if (candidate / ".git").exists():
            raise RepositoryMaintenanceBlocked("ARTIFACT_ROOT_IS_REPOSITORY_PATH")


def _assert_only_final_artifacts(root: Path) -> None:
    names = sorted(path.name for path in root.iterdir())
    if names != sorted(LAVALAMP_ALLOWED_ARTIFACTS):
        raise RepositoryMaintenanceBlocked("ARTIFACT_ROOT_CONTAINS_EXTRA_FILES")


def _manifest_mismatch_reason(manifest: Mapping[str, object]) -> str | None:
    expected: dict[str, object] = {
        "schema": LAVALAMP_EXECUTOR_SCHEMA,
        "operation": BUILD_AND_LOCAL_OTA_OPERATION,
        "project": LAVALAMP_PROJECT,
        "repository": LAVALAMP_REPOSITORY,
        "source_branch": LAVALAMP_SOURCE_BRANCH,
        "source_sha": LAVALAMP_SOURCE_SHA,
        "wled_commit": LAVALAMP_WLED_SHA,
        "environment": LAVALAMP_PLATFORMIO_ENV,
        "artifact_root": str(LAVALAMP_ARTIFACT_ROOT),
        "artifact_files": list(LAVALAMP_ALLOWED_ARTIFACTS),
        "relay": LAVALAMP_RELAY,
        "target": LAVALAMP_TARGET,
        "no_direct_controller_lan_ota": True,
        "required_effects": list(LAVALAMP_REQUIRED_EFFECTS),
        "approval_reference": LAVALAMP_APPROVAL_REFERENCE,
        "idempotency_key": LAVALAMP_IDEMPOTENCY_KEY,
    }
    for key, expected_value in expected.items():
        if manifest.get(key) != expected_value:
            return f"{key.upper()}_MISMATCH"
    if manifest.get("status") not in {"BUILT", "DONE"}:
        return "STATUS_MISMATCH"
    byte_size = manifest.get("byte_size")
    digest = manifest.get("sha256")
    if not isinstance(byte_size, int) or byte_size <= 0 or byte_size > LAVALAMP_MAX_FIRMWARE_BYTES:
        return "BYTE_SIZE_MISMATCH"
    if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        return "SHA256_MISMATCH"
    return None


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _atomic_copy(source: Path, destination: Path) -> None:
    tmp = destination.with_name(destination.name + ".tmp")
    shutil.copy2(source, tmp)
    tmp.replace(destination)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _blocked_receipt(reason: str) -> dict[str, object]:
    return {
        "schema": LAVALAMP_EXECUTOR_SCHEMA,
        "status": "BLOCKED",
        "operation": BUILD_AND_LOCAL_OTA_OPERATION,
        "project": LAVALAMP_PROJECT,
        "repository": LAVALAMP_REPOSITORY,
        "reason": re.sub(r"[^A-Z0-9_]+", "_", reason.upper()).strip("_") or "BLOCKED",
        "no_filesystem_build_or_network_after_rejection": True,
        "public_status": "blocked_before_untrusted_side_effect",
    }


def _public_receipt(manifest: Mapping[str, object], ota_receipt: Mapping[str, object], duplicate: bool) -> dict[str, object]:
    return {
        "schema": LAVALAMP_EXECUTOR_SCHEMA,
        "status": "DONE",
        "operation": BUILD_AND_LOCAL_OTA_OPERATION,
        "project": LAVALAMP_PROJECT,
        "repository": LAVALAMP_REPOSITORY,
        "source_branch": LAVALAMP_SOURCE_BRANCH,
        "source_sha": LAVALAMP_SOURCE_SHA,
        "wled_commit": LAVALAMP_WLED_SHA,
        "environment": LAVALAMP_PLATFORMIO_ENV,
        "artifact_root": str(LAVALAMP_ARTIFACT_ROOT),
        "artifact_files": list(LAVALAMP_ALLOWED_ARTIFACTS),
        "byte_size": manifest.get("byte_size"),
        "sha256": manifest.get("sha256"),
        "approval_reference": LAVALAMP_APPROVAL_REFERENCE,
        "idempotency_key": LAVALAMP_IDEMPOTENCY_KEY,
        "relay": LAVALAMP_RELAY,
        "target": LAVALAMP_TARGET,
        "no_direct_controller_lan_ota": True,
        "duplicate_verified_completion": duplicate,
        "ota": dict(ota_receipt),
        "public_status": "done",
    }


def _repository_result(status: str, receipt: Mapping[str, object]) -> str:
    return (
        f"RESULT: {status}\n"
        "Executor: repository_maintenance.build_and_local_ota\n"
        "Model providers called: 0\n"
        "Receipt:\n"
        f"{json.dumps(receipt, indent=2, sort_keys=True)}"
    )


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
