from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from core.home_edge.firmware_action import (
    FirmwareTransferRequest,
    HomeEdgeFirmwareAction,
    HomeEdgeFirmwareActionError,
)
from core.project_tree import get_project, load_project_tree
from core.runner_executor import RunnerExecutorError, validate_executor_task
from core.runner_task import RunnerTask


TASK_KIND: Final = "repository_maintenance"
OPERATION: Final = "build_and_local_ota"
PROJECT: Final = "lavalamp"
REPOSITORY: Final = "alanua/Lavalamp"
SOURCE_BRANCH: Final = "main"
SOURCE_SHA: Final = "c98acbf12c51492bee32e1ab07dd349752e4bee5"
WLED_REPOSITORY: Final = "https://github.com/wled/WLED.git"
WLED_SHA: Final = "58a84b653672b3611bc90cbf1b52bd1615132468"
PLATFORMIO_ENV: Final = "cylinder_lava_esp32"
ARTIFACT_ROOT: Final = Path("/home/agent/agent-dev/artifacts/lavalamp/source-issue-2")
APPROVED_ARTIFACT_PARENT: Final = Path("/home/agent/agent-dev/artifacts/lavalamp")
FIRMWARE_NAME: Final = "firmware.bin"
MANIFEST_NAME: Final = "manifest.json"
HOME_EDGE_NODE: Final = "home-edge-01"
DEVICE_TARGET: Final = "192.168.1.164"
APPROVAL_REFERENCE: Final = "EXPLICIT_APPROVE_LAVALAMP_FIRMWARE_AND_LOCAL_OTA_20260724"
IDEMPOTENCY_KEY: Final = "lavalamp-c98acbf-build-ota-1922"
POSTFLIGHT_EFFECTS: Final = ("CY Anemone", "CY Tidal Bloom")
ALLOWED_CAPABILITY_SETS: Final = frozenset(
    (
        frozenset(("repository_read", "repository_maintenance")),
        frozenset(("repository_read", "repository_maintenance", "test_execution")),
        frozenset(("repository_read", "repository_maintenance", "subprocess_isolated")),
        frozenset(
            (
                "repository_read",
                "repository_maintenance",
                "test_execution",
                "subprocess_isolated",
            )
        ),
    )
)
ALLOWED_FILES: Final = (FIRMWARE_NAME, MANIFEST_NAME)
MAX_FIRMWARE_BYTES: Final = 4 * 1024 * 1024
BUILD_TIMEOUT_SECONDS: Final = 3600
SCHEMA: Final = "skeleton.repository_maintenance.lavalamp_build_ota.v1"


class RepositoryMaintenanceBlocked(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


RunCommand = Callable[
    [list[str], Path | None, int | None, Mapping[str, str] | None],
    tuple[int, str],
]


def _run_command(
    args: list[str],
    cwd: Path | None,
    timeout: int | None,
    env: Mapping[str, str] | None,
) -> tuple[int, str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    completed = subprocess.run(
        args,
        cwd=str(cwd) if cwd is not None else None,
        env=merged_env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return completed.returncode, (completed.stdout or "")


@dataclass(frozen=True)
class RepositoryMaintenanceExecutor:
    project_tree_path: Path = Path(__file__).resolve().parents[1] / "PROJECT_TREE.yaml"
    run_command: RunCommand = _run_command
    firmware_action: HomeEdgeFirmwareAction | None = None
    task_kind: str = TASK_KIND
    required_capabilities: tuple[str, ...] = (
        "repository_maintenance",
        "repository_read",
    )

    def execute(self, task: RunnerTask) -> tuple[int, str]:
        try:
            validate_executor_task(self.task_kind, task)
            self._validate_fixed_task(task)
            source_checkout = self._verified_source_checkout()
            artifact_root = self._validated_artifact_root()
            manifest_path = artifact_root / MANIFEST_NAME
            firmware_path = artifact_root / FIRMWARE_NAME
            reused_manifest = self._verified_existing_manifest(manifest_path, firmware_path)
            if reused_manifest is None:
                manifest = self._build_artifact(source_checkout, artifact_root)
            else:
                manifest = reused_manifest

            request = FirmwareTransferRequest(
                firmware_path=firmware_path,
                byte_size=int(manifest["byte_size"]),
                sha256=str(manifest["sha256"]),
                relay=HOME_EDGE_NODE,
                target=DEVICE_TARGET,
                postflight_effects=POSTFLIGHT_EFFECTS,
                idempotency_key=IDEMPOTENCY_KEY,
            )
            action = self.firmware_action or HomeEdgeFirmwareAction()
            if manifest.get("ota", {}).get("final_status") == "DONE":
                ota_receipt = action.verify_postflight_only(request)
                duplicate = True
            else:
                ota_receipt = action.execute(request)
                duplicate = False
                manifest = {
                    **manifest,
                    "ota": ota_receipt,
                    "updated_at": _utc_now(),
                    "status": "DONE",
                }
                _atomic_write_json(manifest_path, manifest)
            return 0, _result("DONE", _receipt(manifest, ota_receipt, duplicate))
        except RepositoryMaintenanceBlocked as exc:
            return 0, _result("BLOCKED", _blocked_receipt(exc.reason_code))
        except HomeEdgeFirmwareActionError as exc:
            return 0, _result("BLOCKED", _blocked_receipt(exc.reason_code))
        except RunnerExecutorError:
            raise
        except Exception:
            return 0, _result("BLOCKED", _blocked_receipt("REPOSITORY_MAINTENANCE_FAILED"))

    def _validate_fixed_task(self, task: RunnerTask) -> None:
        payload = task.payload
        expected_fields = {
            "operation": OPERATION,
            "project": PROJECT,
            "repository": REPOSITORY,
            "source_branch": SOURCE_BRANCH,
            "source_sha": SOURCE_SHA,
            "wled_repository": WLED_REPOSITORY,
            "wled_sha": WLED_SHA,
            "platformio_env": PLATFORMIO_ENV,
            "artifact_root": str(ARTIFACT_ROOT),
            "relay": HOME_EDGE_NODE,
            "target": DEVICE_TARGET,
            "idempotency_key": IDEMPOTENCY_KEY,
        }
        if set(payload) != set(expected_fields):
            raise RepositoryMaintenanceBlocked("PACKET_FIELD_SET_MISMATCH")
        for field, expected in expected_fields.items():
            if payload.get(field) != expected:
                raise RepositoryMaintenanceBlocked(f"{field.upper()}_MISMATCH")
        if task.repo != REPOSITORY:
            raise RepositoryMaintenanceBlocked("TASK_REPOSITORY_MISMATCH")
        if task.branch != SOURCE_BRANCH or task.base_sha != SOURCE_SHA:
            raise RepositoryMaintenanceBlocked("TASK_SOURCE_REF_MISMATCH")
        if task.approval_reference != APPROVAL_REFERENCE:
            raise RepositoryMaintenanceBlocked("APPROVAL_REFERENCE_MISMATCH")
        if task.idempotency_key != IDEMPOTENCY_KEY:
            raise RepositoryMaintenanceBlocked("TASK_IDEMPOTENCY_KEY_MISMATCH")
        if frozenset(task.requested_capabilities) not in ALLOWED_CAPABILITY_SETS:
            raise RepositoryMaintenanceBlocked("CAPABILITY_SET_MISMATCH")
        if tuple(task.allowed_files) != ALLOWED_FILES:
            raise RepositoryMaintenanceBlocked("ARTIFACT_ALLOWLIST_MISMATCH")
        if task.privacy_boundary != "PUBLIC_SAFE_REPOSITORY_ONLY":
            raise RepositoryMaintenanceBlocked("PRIVACY_BOUNDARY_MISMATCH")

    def _verified_source_checkout(self) -> Path:
        try:
            project_tree = load_project_tree(self.project_tree_path)
            project = get_project(project_tree, PROJECT)
        except Exception as exc:
            raise RepositoryMaintenanceBlocked("PROJECT_REGISTRY_UNAVAILABLE") from exc
        if project.get("repo") != REPOSITORY:
            raise RepositoryMaintenanceBlocked("PROJECT_REGISTRY_REPOSITORY_MISMATCH")
        checkout = Path(str(project.get("checkout_path", ""))).resolve()
        if not checkout.is_dir() or checkout.is_symlink():
            raise RepositoryMaintenanceBlocked("SOURCE_CHECKOUT_UNAVAILABLE")
        if _git(self.run_command, ["config", "--get", "remote.origin.url"], checkout) not in {
            "git@github.com:alanua/Lavalamp.git",
            "https://github.com/alanua/Lavalamp.git",
        }:
            raise RepositoryMaintenanceBlocked("SOURCE_ORIGIN_MISMATCH")
        if _git(self.run_command, ["branch", "--show-current"], checkout) != SOURCE_BRANCH:
            raise RepositoryMaintenanceBlocked("SOURCE_BRANCH_MISMATCH")
        if _git(self.run_command, ["rev-parse", "HEAD"], checkout) != SOURCE_SHA:
            raise RepositoryMaintenanceBlocked("SOURCE_SHA_MISMATCH")
        if _git(self.run_command, ["status", "--porcelain=v1", "--untracked-files=all"], checkout):
            raise RepositoryMaintenanceBlocked("SOURCE_CHECKOUT_DIRTY")
        return checkout

    def _validated_artifact_root(self) -> Path:
        root = ARTIFACT_ROOT
        parent = APPROVED_ARTIFACT_PARENT
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
        if _is_repository_path(resolved_root):
            raise RepositoryMaintenanceBlocked("ARTIFACT_ROOT_IS_REPOSITORY_PATH")
        resolved_root.mkdir(parents=True, exist_ok=True)
        if resolved_root.is_symlink():
            raise RepositoryMaintenanceBlocked("ARTIFACT_ROOT_SYMLINK")
        return resolved_root

    def _verified_existing_manifest(
        self, manifest_path: Path, firmware_path: Path
    ) -> dict[str, object] | None:
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

    def _build_artifact(self, source_checkout: Path, artifact_root: Path) -> dict[str, object]:
        temp_dir: Path | None = None
        try:
            temp_dir = Path(
                tempfile.mkdtemp(prefix="build-", dir=str(artifact_root))
            ).resolve()
            wled_dir = temp_dir / "WLED"
            _ok(self.run_command(["git", "init", str(wled_dir)], None, 30, None), "WLED_INIT_FAILED")
            _ok(self.run_command(["git", "remote", "add", "origin", WLED_REPOSITORY], wled_dir, 30, None), "WLED_REMOTE_FAILED")
            _ok(self.run_command(["git", "fetch", "--depth", "1", "origin", WLED_SHA], wled_dir, 600, None), "WLED_FETCH_FAILED")
            _ok(self.run_command(["git", "checkout", "--detach", WLED_SHA], wled_dir, 30, None), "WLED_CHECKOUT_FAILED")
            if _git(self.run_command, ["rev-parse", "HEAD"], wled_dir) != WLED_SHA:
                raise RepositoryMaintenanceBlocked("WLED_HEAD_MISMATCH")
            if _git(self.run_command, ["config", "--get", "remote.origin.url"], wled_dir) != WLED_REPOSITORY:
                raise RepositoryMaintenanceBlocked("WLED_ORIGIN_MISMATCH")

            self._apply_overlay(source_checkout, wled_dir)
            self._verify_effect_registration(wled_dir)
            command = _platformio_command(self.run_command)
            env = {
                "PLATFORMIO_CORE_DIR": str(temp_dir / "pio-core"),
                "PLATFORMIO_BUILD_CACHE_DIR": str(temp_dir / "pio-build-cache"),
                "PLATFORMIO_GLOBALLIB_DIR": str(temp_dir / "pio-lib"),
                "PLATFORMIO_PACKAGES_DIR": str(temp_dir / "pio-packages"),
            }
            _ok(
                self.run_command(
                    [*command, "run", "-e", PLATFORMIO_ENV],
                    wled_dir,
                    BUILD_TIMEOUT_SECONDS,
                    env,
                ),
                "PLATFORMIO_BUILD_FAILED",
            )
            built = wled_dir / ".pio" / "build" / PLATFORMIO_ENV / FIRMWARE_NAME
            size, digest = self._verify_built_firmware(built)
            firmware_dest = artifact_root / FIRMWARE_NAME
            manifest_dest = artifact_root / MANIFEST_NAME
            _atomic_copy(built, firmware_dest)
            manifest = {
                "schema": SCHEMA,
                "status": "BUILT",
                "operation": OPERATION,
                "project": PROJECT,
                "repository": REPOSITORY,
                "source_branch": SOURCE_BRANCH,
                "source_sha": SOURCE_SHA,
                "wled_repository": WLED_REPOSITORY,
                "wled_sha": WLED_SHA,
                "platformio_env": PLATFORMIO_ENV,
                "artifact_root": str(ARTIFACT_ROOT),
                "artifact_files": list(ALLOWED_FILES),
                "firmware_path": str(firmware_dest),
                "byte_size": size,
                "sha256": digest,
                "build_command": " ".join([*command, "run", "-e", PLATFORMIO_ENV]),
                "build_timeout_seconds": BUILD_TIMEOUT_SECONDS,
                "relay": HOME_EDGE_NODE,
                "target": DEVICE_TARGET,
                "no_direct_controller_lan_ota": True,
                "postflight_effects": list(POSTFLIGHT_EFFECTS),
                "idempotency_key": IDEMPOTENCY_KEY,
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
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

    def _apply_overlay(self, source_checkout: Path, wled_dir: Path) -> None:
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
        code, _output = self.run_command(
            ["git", "apply", "--whitespace=nowarn", str(patch)],
            wled_dir,
            30,
            None,
        )
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
            if path.is_file():
                haystack.append(path.read_text(encoding="utf-8", errors="ignore"))
            elif path.is_dir():
                for child in path.rglob("*"):
                    if child.is_file() and not child.is_symlink():
                        haystack.append(child.read_text(encoding="utf-8", errors="ignore"))
        text = "\n".join(haystack)
        if "cylinder_lava" not in text or not all(effect in text for effect in POSTFLIGHT_EFFECTS):
            raise RepositoryMaintenanceBlocked("LAVALAMP_EFFECT_REGISTRATION_MISSING")

    def _verify_built_firmware(self, path: Path) -> tuple[int, str]:
        if not path.is_file() or path.is_symlink():
            raise RepositoryMaintenanceBlocked("FIRMWARE_OUTPUT_MISSING")
        size = path.stat().st_size
        if size <= 0:
            raise RepositoryMaintenanceBlocked("FIRMWARE_OUTPUT_EMPTY")
        if size > MAX_FIRMWARE_BYTES:
            raise RepositoryMaintenanceBlocked("FIRMWARE_OUTPUT_OVERSIZED")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return size, digest

    def _verify_firmware_file(self, path: Path, digest: str, size: int) -> None:
        actual_size, actual_digest = self._verify_built_firmware(path)
        if actual_size != size or actual_digest != digest:
            raise RepositoryMaintenanceBlocked("FIRMWARE_ARTIFACT_MISMATCH")


def _manifest_mismatch_reason(manifest: Mapping[str, object]) -> str | None:
    expected: dict[str, object] = {
        "schema": SCHEMA,
        "operation": OPERATION,
        "project": PROJECT,
        "repository": REPOSITORY,
        "source_branch": SOURCE_BRANCH,
        "source_sha": SOURCE_SHA,
        "wled_repository": WLED_REPOSITORY,
        "wled_sha": WLED_SHA,
        "platformio_env": PLATFORMIO_ENV,
        "artifact_root": str(ARTIFACT_ROOT),
        "artifact_files": list(ALLOWED_FILES),
        "relay": HOME_EDGE_NODE,
        "target": DEVICE_TARGET,
        "no_direct_controller_lan_ota": True,
        "postflight_effects": list(POSTFLIGHT_EFFECTS),
        "idempotency_key": IDEMPOTENCY_KEY,
    }
    for key, expected_value in expected.items():
        if manifest.get(key) != expected_value:
            return key
    if not isinstance(manifest.get("byte_size"), int) or not isinstance(manifest.get("sha256"), str):
        return "artifact_identity"
    return None


def _platformio_command(run_command: RunCommand) -> tuple[str, ...]:
    code, _output = run_command(["pio", "--version"], None, 15, None)
    if code == 0:
        return ("pio",)
    code, _output = run_command(["python3", "-m", "platformio", "--version"], None, 15, None)
    if code == 0:
        return ("python3", "-m", "platformio")
    raise RepositoryMaintenanceBlocked("PLATFORMIO_UNAVAILABLE")


def _git(run_command: RunCommand, args: list[str], cwd: Path) -> str:
    code, output = run_command(["git", *args], cwd, 30, None)
    if code != 0:
        raise RepositoryMaintenanceBlocked("GIT_PREFLIGHT_FAILED")
    return output.strip()


def _ok(result: tuple[int, str], reason_code: str) -> None:
    if result[0] != 0:
        raise RepositoryMaintenanceBlocked(reason_code)


def _is_repository_path(path: Path) -> bool:
    current = path
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return True
    return False


def _assert_only_final_artifacts(root: Path) -> None:
    names = sorted(path.name for path in root.iterdir())
    if names != sorted(ALLOWED_FILES):
        raise RepositoryMaintenanceBlocked("ARTIFACT_ROOT_CONTAINS_EXTRA_FILES")
    for name in ALLOWED_FILES:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise RepositoryMaintenanceBlocked("ARTIFACT_ROOT_CONTAINS_EXTRA_FILES")


def _atomic_copy(source: Path, destination: Path) -> None:
    temp = destination.with_name(destination.name + ".tmp")
    shutil.copy2(source, temp)
    os.replace(temp, destination)


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _receipt(
    manifest: Mapping[str, object],
    ota_receipt: Mapping[str, object],
    duplicate: bool,
) -> dict[str, object]:
    effects = ota_receipt.get("effects") if isinstance(ota_receipt.get("effects"), Mapping) else {}
    return {
        "schema": "skeleton.repository_maintenance.receipt.v1",
        "status": "DONE",
        "artifact_path": str(ARTIFACT_ROOT / FIRMWARE_NAME),
        "manifest_path": str(ARTIFACT_ROOT / MANIFEST_NAME),
        "byte_size": manifest.get("byte_size"),
        "sha256": manifest.get("sha256"),
        "source_sha": SOURCE_SHA,
        "wled_sha": WLED_SHA,
        "build_status": manifest.get("status", "BUILT"),
        "cleanup_status": manifest.get("cleanup_status"),
        "relay": HOME_EDGE_NODE,
        "target": DEVICE_TARGET,
        "no_direct_controller_lan_ota": True,
        "postflight_effects": {
            effect: effects.get(effect) is True for effect in POSTFLIGHT_EFFECTS
        },
        "final_status": ota_receipt.get("final_status"),
        "idempotent_duplicate": duplicate,
    }


def _blocked_receipt(reason_code: str) -> dict[str, object]:
    return {
        "schema": "skeleton.repository_maintenance.receipt.v1",
        "status": "BLOCKED",
        "reason_codes": [reason_code],
        "source_sha": SOURCE_SHA,
        "wled_sha": WLED_SHA,
        "relay": HOME_EDGE_NODE,
        "target": DEVICE_TARGET,
        "no_direct_controller_lan_ota": True,
        "final_status": "BLOCKED",
    }


def _result(status: str, payload: Mapping[str, object]) -> str:
    return f"RESULT: {status}\n" + json.dumps(payload, sort_keys=True)
