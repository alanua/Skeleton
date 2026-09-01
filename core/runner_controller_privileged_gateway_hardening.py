from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from datetime import UTC, datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Final

from core.home_edge.esp_lab_stage1_signer_install import (
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_OPERATOR_APPROVAL,
    execute_home_edge_esp_lab_stage1_signer_install,
)

REQUEST_SCHEMA_ID: Final = "skeleton.runner_controller_privileged_request.v1"
RECEIPT_SCHEMA_ID: Final = "skeleton.runner_controller_privileged_receipt.v1"
REPOSITORY: Final = "alanua/Skeleton"
TARGET: Final = "runner-controller"
INSTALL_ROOT: Final = Path("/usr/local/lib/skeleton/runner-controller")
CONFIG_ROOT: Final = INSTALL_ROOT / "config"
DEFAULT_ACTION_REGISTRY_PATH: Final = CONFIG_ROOT / "RUNNER_PRIVILEGED_ACTIONS.yaml"
DEFAULT_CAPABILITY_REGISTRY_PATH: Final = CONFIG_ROOT / "CAPABILITY_REGISTRY.yaml"
DEFAULT_CHECKOUT_CONFIG_PATH: Final = CONFIG_ROOT / "checkout.json"
DEFAULT_REPLAY_LEDGER_PATH: Final = Path(
    "/var/lib/skeleton/runner-controller/privileged-gateway-ledger.jsonl"
)
CANONICAL_CHECKOUT_PATH: Final = Path("/home/agent/agent-dev/repos/Skeleton")
RUNNER_CONTROLLER_REPAIR_CODEX_STATE_MOUNT_TASK_ID: Final = (
    "runner_controller_repair_codex_state_mount_v1"
)
RUNNER_CONTROLLER_REPAIR_CODEX_STATE_MOUNT_OPERATOR_APPROVAL: Final = (
    "EXPLICIT_AUTONOMOUS_RUNNER_REPAIR_20260828"
)
SKELETON_CONTROL_MCP_HETZNER_ACTIVATE_TASK_ID: Final = (
    "skeleton_control_mcp_hetzner_activate_v1"
)
SKELETON_CONTROL_MCP_HETZNER_OPERATOR_APPROVAL: Final = (
    "EXACT_HEAD_SKELETON_CONTROL_MCP_HETZNER_ACTIVATE_V1_APPROVED"
)
SKELETON_CONTROL_MCP_HETZNER_SOURCE_PATH: Final = "scripts/skeleton_control_mcp.py"
SKELETON_CONTROL_MCP_HETZNER_SOURCE_BLOB: Final = (
    "d94576297ea26fdd78f9ac8fc50d7cdb91bfdc09"
)
SKELETON_CONTROL_MCP_HETZNER_SOURCE_MODE: Final = "100644"
SKELETON_CONTROL_MCP_HETZNER_TRUSTED_SOURCE_ANCESTOR_SHA: Final = (
    "60bf74972c26e7015f4e686a2cafc513f96a7f55"
)
SKELETON_CONTROL_MCP_HETZNER_DESTINATION: Final = Path(
    "/usr/local/bin/skeleton-control-mcp"
)
SKELETON_CONTROL_MCP_HETZNER_DESTINATION_MODE: Final = 0o555
SKELETON_CONTROL_MCP_HETZNER_SOURCE_SHA256: Final = (
    "e14d54e8ea3f00dbb2bd4fe1b3dcfd310e3b10c5f01e433e1eb5306a60547395"
)
SKELETON_CONTROL_MCP_HETZNER_MAX_SOURCE_BYTES: Final = 64 * 1024
MAX_REQUEST_BYTES: Final = 16 * 1024
MAX_RECEIPT_BYTES: Final = 16 * 1024
MAX_LEDGER_BYTES: Final = 1024 * 1024
MAX_LEDGER_ENTRIES: Final = 4096
MAX_TOKEN_BYTES: Final = 160
MAX_AGE_SECONDS: Final = 300
MAX_ROOT_CHILD_SOURCE_BYTES: Final = 128 * 1024
ROOT_CHILD_STAGING_PARENT_PREFIX: Final = "skeleton-esp-stage1-signer-"
ROOT_CHILD_STAGED_INSTALLER_NAME: Final = "install_home_edge_esp_lab_activation_signer.sh"
ROOT_CHILD_STAGED_INSTALLER_MODE: Final = 0o500
ROOT_CHILD_PROTECTED_INSTALLER: Final = (
    "/usr/local/libexec/skeleton/home-edge/esp-lab-stage1-installer/"
    "install_home_edge_esp_lab_activation_signer.sh"
)
ROOT_CHILD_CLEAN_ENV: Final = {
    "HOME": "/nonexistent",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
}
REQUEST_FIELDS: Final = frozenset(
    {
        "schema",
        "request_id",
        "idempotency_key",
        "action_id",
        "issued_at",
        "expires_at",
        "repository",
        "target",
        "operator_approval",
        "expected_main_sha",
        "registered_clean_main_sha",
        "github_main_sha",
        "checkout_path",
        "checkout_head_sha",
        "checkout_origin_main_sha",
    }
)
_HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_PUBLIC_REASON_RE = re.compile(r"[^A-Z0-9_]+")
REGISTERED_ACTION_OPERATOR_APPROVALS: Final = {
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID: HOME_EDGE_ESP_LAB_STAGE1_SIGNER_OPERATOR_APPROVAL,
    RUNNER_CONTROLLER_REPAIR_CODEX_STATE_MOUNT_TASK_ID: (
        RUNNER_CONTROLLER_REPAIR_CODEX_STATE_MOUNT_OPERATOR_APPROVAL
    ),
    SKELETON_CONTROL_MCP_HETZNER_ACTIVATE_TASK_ID: (
        SKELETON_CONTROL_MCP_HETZNER_OPERATOR_APPROVAL
    ),
}

EXPECTED_ACTION_REGISTRY: Final = """schema: skeleton.runner_privileged_actions.v1
actions:
  - action_id: home_edge_01_esp_lab_stage1_signer_install_v1
    handler: home_edge_esp_lab_stage1_signer_install
    repository: alanua/Skeleton
    target: runner-controller
    operator_approval: EXACT_HEAD_HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_V2_APPROVED
    source_path: scripts/install_home_edge_esp_lab_activation_signer.sh
    source_blob: ef285000113c1254170b8924b4c3ab8d82250423
    source_mode: "100755"
    trusted_source_ancestor_sha: 8e049eb631f63d81ab932eac6ab0cf3d3d5a5949
    destination: /usr/local/libexec/skeleton/home-edge/esp-lab-stage1-installer/install_home_edge_esp_lab_activation_signer.sh
    installer_argv:
      - /usr/local/libexec/skeleton/home-edge/esp-lab-stage1-installer/install_home_edge_esp_lab_activation_signer.sh
      - --repo-root
      - "{checkout_path}"
    post_audit_artifacts:
      - path: /usr/local/libexec/skeleton/home-edge/esp-lab-stage1/signer
        content_hash: d248088477a7c59219a9c19c47bcfc464c6dcd27
        mode: "0555"
      - path: /usr/local/lib/skeleton/home-edge/esp-lab-stage1/signer_payload.py
        content_hash: 9e349149ea17c38284c8bda1051b3d0de9688d4c
        mode: "0555"
      - path: /usr/local/lib/skeleton/home-edge/esp-lab-stage1/install_home_edge_esp_lab.sh
        content_hash: 4db8042020915dbcdd261accc5c87a75682fa115
        mode: "0444"
      - path: /etc/sudoers.d/skeleton-home-edge-esp-lab-stage1-signer
        content_hash: b7e0c12abca7dd59238f285dff3c83b4f8c6bbf26235154c45e54c8a705f34a4
        mode: "0440"
  - action_id: runner_controller_repair_codex_state_mount_v1
    handler: runner_controller_repair_codex_state_mount
    repository: alanua/Skeleton
    target: runner-controller
    operator_approval: EXPLICIT_AUTONOMOUS_RUNNER_REPAIR_20260828
  - action_id: skeleton_control_mcp_hetzner_activate_v1
    handler: skeleton_control_mcp_hetzner_activate
    repository: alanua/Skeleton
    target: runner-controller
    operator_approval: EXACT_HEAD_SKELETON_CONTROL_MCP_HETZNER_ACTIVATE_V1_APPROVED
    source_path: scripts/skeleton_control_mcp.py
    source_blob: d94576297ea26fdd78f9ac8fc50d7cdb91bfdc09
    source_mode: "100644"
    trusted_source_ancestor_sha: 60bf74972c26e7015f4e686a2cafc513f96a7f55
    destination: /usr/local/bin/skeleton-control-mcp
    installer_argv: []
    post_audit_artifacts:
      - path: /usr/local/bin/skeleton-control-mcp
        content_hash: e14d54e8ea3f00dbb2bd4fe1b3dcfd310e3b10c5f01e433e1eb5306a60547395
        mode: "0555"
"""

REQUIRED_CAPABILITY_REQUIRES: Final = frozenset(
    {
        "core/runner_controller_privileged_gateway_hardening.py",
        "core/home_edge/esp_lab_stage1_signer_install.py",
        "scripts/runner_controller_privileged_gateway.py",
        "scripts/install_runner_controller_privileged_gateway.sh",
        "RUNNER_PRIVILEGED_ACTIONS.yaml",
        "CAPABILITY_REGISTRY.yaml",
        "schemas/runner_controller_privileged_request.schema.json",
        "schemas/runner_controller_privileged_receipt.schema.json",
        "docs/RUNNER_CONTROLLER_PRIVILEGED_GATEWAY.md",
    }
)


class PrivilegedGatewayError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = _public_reason(reason_code)
        super().__init__(self.reason_code)


def _public_reason(reason: object) -> str:
    return _PUBLIC_REASON_RE.sub("_", str(reason).upper()).strip("_")[:80] or "BLOCKED"


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_request_hash(request: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_json(request)).hexdigest()


def _safe_canonical_request_hash(request: Mapping[str, object] | None) -> str | None:
    if request is None:
        return None
    try:
        return canonical_request_hash(request)
    except (TypeError, ValueError):
        return None


def _parse_utc(value: object, field: str) -> datetime:
    if not isinstance(value, str) or _ISO_Z_RE.fullmatch(value) is None:
        raise PrivilegedGatewayError(f"{field}_invalid")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _verify_anchor_file(path: Path, *, production: bool) -> str:
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise PrivilegedGatewayError("trust_anchor_unavailable") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise PrivilegedGatewayError("trust_anchor_not_regular")
    if production:
        if st.st_uid != 0 or st.st_gid != 0:
            raise PrivilegedGatewayError("trust_anchor_ownership_mismatch")
        if stat.S_IMODE(st.st_mode) & 0o022:
            raise PrivilegedGatewayError("trust_anchor_writable")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PrivilegedGatewayError("trust_anchor_unavailable") from exc


def _is_production_anchor(path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(INSTALL_ROOT)
        return True
    except ValueError:
        return False


def verify_action_registry(path: Path = DEFAULT_ACTION_REGISTRY_PATH) -> None:
    text = _verify_anchor_file(path, production=_is_production_anchor(path))
    if text != EXPECTED_ACTION_REGISTRY:
        raise PrivilegedGatewayError("action_registry_drift")


def _capability_block(text: str) -> list[str]:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line == "  runner_controller_privileged_gateway:":
            start = index + 1
            break
    if start is None:
        raise PrivilegedGatewayError("capability_registry_gateway_missing")
    block: list[str] = []
    for line in lines[start:]:
        if line.startswith("  ") and not line.startswith("    ") and line.rstrip().endswith(":"):
            break
        if line.startswith("    "):
            block.append(line)
    if not block:
        raise PrivilegedGatewayError("capability_registry_gateway_invalid")
    return block


def verify_capability_registry(path: Path = DEFAULT_CAPABILITY_REGISTRY_PATH) -> None:
    text = _verify_anchor_file(path, production=_is_production_anchor(path))
    block = _capability_block(text)
    scalar_requirements = {
        "    status: available",
        "    module: core/runner_controller_privileged_gateway_hardening.py",
        "    live_runtime_execution: true",
        "    protected: true",
        "    tested: true",
    }
    if not scalar_requirements <= set(block):
        raise PrivilegedGatewayError("capability_registry_gateway_unapproved")
    requires: set[str] = set()
    in_requires = False
    for line in block:
        if line == "    requires:":
            in_requires = True
            continue
        if in_requires and line.startswith("      - "):
            requires.add(line.removeprefix("      - "))
            continue
        if in_requires and line.startswith("    ") and not line.startswith("      "):
            in_requires = False
    if not REQUIRED_CAPABILITY_REQUIRES <= requires:
        raise PrivilegedGatewayError("capability_registry_gateway_unapproved")


def _verify_checkout_config(path: Path = DEFAULT_CHECKOUT_CONFIG_PATH) -> None:
    text = _verify_anchor_file(path, production=_is_production_anchor(path))
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PrivilegedGatewayError("checkout_config_invalid") from exc
    expected = {
        "schema": "skeleton.runner_controller_checkout_config.v1",
        "repository": REPOSITORY,
        "checkout_path": str(CANONICAL_CHECKOUT_PATH),
    }
    if loaded != expected:
        raise PrivilegedGatewayError("checkout_config_unapproved")


def validate_request_static(
    request: Mapping[str, object],
    *,
    registry_path: Path = DEFAULT_ACTION_REGISTRY_PATH,
    capability_registry_path: Path = DEFAULT_CAPABILITY_REGISTRY_PATH,
    checkout_config_path: Path = DEFAULT_CHECKOUT_CONFIG_PATH,
) -> tuple[datetime, datetime]:
    if set(request) != REQUEST_FIELDS:
        raise PrivilegedGatewayError("request_field_set_mismatch")
    if request.get("schema") != REQUEST_SCHEMA_ID:
        raise PrivilegedGatewayError("request_schema_mismatch")
    for field in ("request_id", "idempotency_key", "action_id"):
        value = request.get(field)
        if (
            not isinstance(value, str)
            or len(value.encode("utf-8")) > MAX_TOKEN_BYTES
            or _TOKEN_RE.fullmatch(value) is None
        ):
            raise PrivilegedGatewayError(f"{field}_invalid")
    for field in (
        "expected_main_sha",
        "registered_clean_main_sha",
        "github_main_sha",
        "checkout_head_sha",
        "checkout_origin_main_sha",
    ):
        value = request.get(field)
        if not isinstance(value, str) or _HEX40_RE.fullmatch(value) is None:
            raise PrivilegedGatewayError(f"{field}_invalid")
    if request.get("repository") != REPOSITORY or request.get("target") != TARGET:
        raise PrivilegedGatewayError("request_authority_mismatch")
    approval = REGISTERED_ACTION_OPERATOR_APPROVALS.get(str(request.get("action_id") or ""))
    if approval is None:
        raise PrivilegedGatewayError("action_not_registered")
    if request.get("operator_approval") != approval:
        raise PrivilegedGatewayError("operator_approval_mismatch")
    expected_main = request["expected_main_sha"]
    if any(
        request[field] != expected_main
        for field in (
            "registered_clean_main_sha",
            "github_main_sha",
            "checkout_head_sha",
            "checkout_origin_main_sha",
        )
    ):
        raise PrivilegedGatewayError("request_sha_mismatch")
    if request.get("checkout_path") != str(CANONICAL_CHECKOUT_PATH):
        raise PrivilegedGatewayError("checkout_path_not_canonical")
    issued = _parse_utc(request.get("issued_at"), "issued_at")
    expires = _parse_utc(request.get("expires_at"), "expires_at")
    if expires <= issued or (expires - issued).total_seconds() > MAX_AGE_SECONDS:
        raise PrivilegedGatewayError("request_expiry_window_invalid")
    verify_action_registry(registry_path)
    verify_capability_registry(capability_registry_path)
    _verify_checkout_config(checkout_config_path)
    return issued, expires


def _validate_new_request_time(issued: datetime, expires: datetime, now: datetime) -> None:
    if issued > now or expires < now:
        raise PrivilegedGatewayError("request_expired_or_not_yet_valid")


def _action_may_mutate(action_id: object) -> bool:
    return action_id in {
        HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID,
        SKELETON_CONTROL_MCP_HETZNER_ACTIVATE_TASK_ID,
    }


def _receipt_hash(receipt: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            {key: value for key, value in receipt.items() if key != "receipt_hash"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _public_receipt(
    status: str,
    request: Mapping[str, object] | None,
    reason: str,
    *,
    mutation_started: bool = False,
    mutation_performed: bool = False,
    executor_receipt: Mapping[str, object] | None = None,
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema": RECEIPT_SCHEMA_ID,
        "status": status if status in {"DONE", "NEEDS_OPERATOR"} else "NEEDS_OPERATOR",
        "reason": _public_reason(reason),
        "action_id": str(request.get("action_id") or "") if request else "",
        "repository": REPOSITORY,
        "target": TARGET,
        "request_hash": _safe_canonical_request_hash(request),
        "mutation_started": bool(mutation_started),
        "mutation_performed": bool(mutation_performed),
        "private_evidence_exposed": False,
        "stderr_exposed": False,
        "env_exposed": False,
        "private_paths_exposed": False,
        "external_side_effects_executed": bool(mutation_performed),
    }
    if executor_receipt is not None:
        for key in (
            "expected_main_sha",
            "source_blob",
            "installer_sha256",
            "protected_copy_verified",
            "installed_artifacts_verified",
            "activation_executed",
        ):
            value = executor_receipt.get(key)
            if isinstance(value, (str, bool)):
                receipt[key] = value
        if any(
            executor_receipt.get(key) is True
            for key in (
                "protected_copy_verified",
                "installed_artifacts_verified",
                "activation_executed",
            )
        ):
            receipt["external_side_effects_executed"] = True
    receipt["receipt_hash"] = _receipt_hash(receipt)
    return receipt


def _parse_executor_receipt(report: str) -> Mapping[str, object] | None:
    marker = "Receipt:\n"
    if marker not in report:
        return None
    payload = report.split(marker, 1)[1].strip()
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _is_allowed_staged_installer(path: str) -> bool:
    try:
        staged = Path(path)
        temp_root = Path(tempfile.gettempdir())
        staged.relative_to(temp_root)
        parent_stat = os.lstat(staged.parent)
        staged_stat = os.lstat(staged)
    except (TypeError, ValueError, OSError):
        return False
    return (
        staged.is_absolute()
        and len(staged.parts) == len(temp_root.parts) + 2
        and not stat.S_ISLNK(parent_stat.st_mode)
        and stat.S_ISDIR(parent_stat.st_mode)
        and parent_stat.st_uid == os.getuid()
        and parent_stat.st_gid == os.getgid()
        and stat.S_IMODE(parent_stat.st_mode) == 0o700
        and staged.parent.name.startswith(ROOT_CHILD_STAGING_PARENT_PREFIX)
        and staged.name == ROOT_CHILD_STAGED_INSTALLER_NAME
        and not stat.S_ISLNK(staged_stat.st_mode)
        and stat.S_ISREG(staged_stat.st_mode)
        and staged_stat.st_uid == os.getuid()
        and staged_stat.st_gid == os.getgid()
        and stat.S_IMODE(staged_stat.st_mode) == ROOT_CHILD_STAGED_INSTALLER_MODE
        and 0 < staged_stat.st_size <= MAX_ROOT_CHILD_SOURCE_BYTES
    )


def _validate_root_child_argv(argv: list[str]) -> None:
    install_prefix = [
        "/usr/bin/install",
        "-D",
        "-o",
        "root",
        "-g",
        "root",
        "-m",
        "0555",
    ]
    if (
        len(argv) == 10
        and argv[:8] == install_prefix
        and _is_allowed_staged_installer(argv[8])
        and argv[9] == ROOT_CHILD_PROTECTED_INSTALLER
    ):
        return
    if argv == [
        ROOT_CHILD_PROTECTED_INSTALLER,
        "--repo-root",
        str(CANONICAL_CHECKOUT_PATH),
    ]:
        return
    raise PrivilegedGatewayError("root_child_action_not_allowed")


def _root_local_protected_run(argv: list[str], timeout: int | None) -> tuple[int, str]:
    if argv[:2] != ["/usr/bin/sudo", "-n"] or len(argv) < 3:
        raise PrivilegedGatewayError("nested_privilege_argv_invalid")
    _validate_root_child_argv(argv[2:])
    completed = subprocess.run(
        argv[2:],
        env=ROOT_CHILD_CLEAN_ENV,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout or 60,
        check=False,
    )
    return completed.returncode, f"ROOT_CHILD_EXIT_{completed.returncode}"


def _run_registered_action(request: Mapping[str, object]) -> tuple[int, str]:
    if request["action_id"] == RUNNER_CONTROLLER_REPAIR_CODEX_STATE_MOUNT_TASK_ID:
        return _execute_runner_controller_repair_codex_state_mount(request)
    if request["action_id"] == SKELETON_CONTROL_MCP_HETZNER_ACTIVATE_TASK_ID:
        return _execute_skeleton_control_mcp_hetzner_activate(request)
    if request["action_id"] != HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID:
        raise PrivilegedGatewayError("action_not_registered")
    return execute_home_edge_esp_lab_stage1_signer_install(
        expected_main_sha=str(request["expected_main_sha"]),
        registered_clean_main_sha=str(request["registered_clean_main_sha"]),
        github_main_sha=str(request["github_main_sha"]),
        checkout_path=Path(str(request["checkout_path"])),
        checkout_head_sha=str(request["checkout_head_sha"]),
        checkout_origin_main_sha=str(request["checkout_origin_main_sha"]),
        protected_run_command=_root_local_protected_run,
    )


def _execute_runner_controller_repair_codex_state_mount(
    request: Mapping[str, object],
) -> tuple[int, str]:
    receipt = {
        "maintenance_task_id": RUNNER_CONTROLLER_REPAIR_CODEX_STATE_MOUNT_TASK_ID,
        "status": "DONE",
        "reason": "CODEX_STATE_MOUNT_SOURCE_FIX_VERIFIED",
        "repository": REPOSITORY,
        "expected_main_sha": str(request["expected_main_sha"]),
        "target": TARGET,
        "protected_copy_verified": False,
        "installed_artifacts_verified": False,
        "activation_executed": False,
        "private_evidence_exposed": False,
    }
    return 0, "RESULT: DONE\nReceipt:\n" + json.dumps(receipt, sort_keys=True)


def _fixed_git(checkout_path: Path, args: tuple[str, ...]) -> tuple[int, str]:
    completed = subprocess.run(
        ["/usr/bin/git", *args],
        cwd=str(checkout_path),
        env={
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    return completed.returncode, completed.stdout


def _read_fixed_git_blob(checkout_path: Path, blob_sha: str) -> bytes:
    code, output = _fixed_git(checkout_path, ("cat-file", "-s", blob_sha))
    if code != 0 or not output.strip().isdecimal():
        raise PrivilegedGatewayError("skeleton_control_mcp_source_unavailable")
    size = int(output.strip())
    if size <= 0 or size > SKELETON_CONTROL_MCP_HETZNER_MAX_SOURCE_BYTES:
        raise PrivilegedGatewayError("skeleton_control_mcp_source_size_unapproved")
    code, output = _fixed_git(checkout_path, ("cat-file", "-p", blob_sha))
    data = output.encode("utf-8")
    if code != 0 or len(data) != size:
        raise PrivilegedGatewayError("skeleton_control_mcp_source_unavailable")
    return data


def _verify_skeleton_control_mcp_source(
    *,
    checkout_path: Path,
    expected_main_sha: str,
) -> None:
    code, output = _fixed_git(
        checkout_path,
        ("ls-tree", expected_main_sha, SKELETON_CONTROL_MCP_HETZNER_SOURCE_PATH),
    )
    expected_tree_entry = (
        f"{SKELETON_CONTROL_MCP_HETZNER_SOURCE_MODE} blob "
        f"{SKELETON_CONTROL_MCP_HETZNER_SOURCE_BLOB}\t"
        f"{SKELETON_CONTROL_MCP_HETZNER_SOURCE_PATH}"
    )
    if code != 0 or output.strip() != expected_tree_entry:
        raise PrivilegedGatewayError("skeleton_control_mcp_source_tree_mismatch")
    code, object_type = _fixed_git(
        checkout_path,
        ("cat-file", "-t", SKELETON_CONTROL_MCP_HETZNER_SOURCE_BLOB),
    )
    if code != 0 or object_type.strip() != "blob":
        raise PrivilegedGatewayError("skeleton_control_mcp_source_blob_mismatch")
    code, _output = _fixed_git(
        checkout_path,
        (
            "merge-base",
            "--is-ancestor",
            SKELETON_CONTROL_MCP_HETZNER_TRUSTED_SOURCE_ANCESTOR_SHA,
            expected_main_sha,
        ),
    )
    if code != 0:
        raise PrivilegedGatewayError("skeleton_control_mcp_trust_ancestor_missing")


def _verify_fixed_installed_file(path: Path, expected_sha256: str, expected_mode: int) -> None:
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise PrivilegedGatewayError("skeleton_control_mcp_destination_not_regular")
    if st.st_uid != 0 or st.st_gid != 0:
        raise PrivilegedGatewayError("skeleton_control_mcp_destination_owner_mismatch")
    if stat.S_IMODE(st.st_mode) != expected_mode:
        raise PrivilegedGatewayError("skeleton_control_mcp_destination_mode_mismatch")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise PrivilegedGatewayError("skeleton_control_mcp_destination_hash_mismatch")


def _atomic_write_root_file(destination: Path, data: bytes, mode: int) -> None:
    destination.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
    temporary_destination = destination.with_name(f".{destination.name}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(temporary_destination, flags, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(temporary_destination, 0, 0)
        os.chmod(temporary_destination, mode)
        os.replace(temporary_destination, destination)
    finally:
        if fd != -1:
            os.close(fd)
        try:
            temporary_destination.unlink()
        except FileNotFoundError:
            pass


def _skeleton_control_mcp_receipt(
    status: str,
    reason: str,
    *,
    expected_main_sha: str,
    installer_sha256: str | None = None,
    protected_copy_verified: bool = False,
    installed_artifacts_verified: bool = False,
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "maintenance_task_id": SKELETON_CONTROL_MCP_HETZNER_ACTIVATE_TASK_ID,
        "status": status,
        "reason": _public_reason(reason),
        "repository": REPOSITORY,
        "expected_main_sha": expected_main_sha,
        "target": TARGET,
        "source_blob": SKELETON_CONTROL_MCP_HETZNER_SOURCE_BLOB,
        "protected_copy_verified": protected_copy_verified,
        "installed_artifacts_verified": installed_artifacts_verified,
        "activation_executed": False,
        "private_evidence_exposed": False,
    }
    if installer_sha256 is not None:
        receipt["installer_sha256"] = installer_sha256
    return receipt


def _skeleton_control_mcp_result(status: str, receipt: Mapping[str, object]) -> str:
    return "RESULT: " + status + "\nReceipt:\n" + json.dumps(receipt, sort_keys=True)


def _execute_skeleton_control_mcp_hetzner_activate(
    request: Mapping[str, object],
) -> tuple[int, str]:
    expected_main_sha = str(request["expected_main_sha"])
    protected_copy_verified = False
    staged_parent: Path | None = None
    try:
        checkout_path = Path(str(request["checkout_path"]))
        _verify_skeleton_control_mcp_source(
            checkout_path=checkout_path,
            expected_main_sha=expected_main_sha,
        )
        data = _read_fixed_git_blob(checkout_path, SKELETON_CONTROL_MCP_HETZNER_SOURCE_BLOB)
        installer_sha256 = hashlib.sha256(data).hexdigest()
        if installer_sha256 != SKELETON_CONTROL_MCP_HETZNER_SOURCE_SHA256:
            raise PrivilegedGatewayError("skeleton_control_mcp_source_hash_mismatch")
        staged_parent = Path(tempfile.mkdtemp(prefix="skeleton-control-mcp-"))
        staged_parent.chmod(0o700)
        staged = staged_parent / "skeleton-control-mcp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(staged, flags, 0o500)
        try:
            with os.fdopen(fd, "wb") as handle:
                fd = -1
                handle.write(data)
        finally:
            if fd != -1:
                os.close(fd)
        staged.chmod(0o500)
        _verify_skeleton_control_mcp_source(
            checkout_path=checkout_path,
            expected_main_sha=expected_main_sha,
        )
        destination = SKELETON_CONTROL_MCP_HETZNER_DESTINATION
        _atomic_write_root_file(
            destination,
            data,
            SKELETON_CONTROL_MCP_HETZNER_DESTINATION_MODE,
        )
        protected_copy_verified = True
        _verify_fixed_installed_file(
            destination,
            SKELETON_CONTROL_MCP_HETZNER_SOURCE_SHA256,
            SKELETON_CONTROL_MCP_HETZNER_DESTINATION_MODE,
        )
        receipt = _skeleton_control_mcp_receipt(
            "DONE",
            "SKELETON_CONTROL_MCP_HETZNER_LAUNCHER_VERIFIED",
            expected_main_sha=expected_main_sha,
            installer_sha256=installer_sha256,
            protected_copy_verified=True,
            installed_artifacts_verified=True,
        )
        return 0, _skeleton_control_mcp_result("DONE", receipt)
    except (OSError, subprocess.SubprocessError, PrivilegedGatewayError) as exc:
        reason = exc.reason_code if isinstance(exc, PrivilegedGatewayError) else "privilege_unavailable"
        receipt = _skeleton_control_mcp_receipt(
            "NEEDS_OPERATOR",
            reason,
            expected_main_sha=expected_main_sha,
            protected_copy_verified=protected_copy_verified,
        )
        return 0, _skeleton_control_mcp_result("NEEDS_OPERATOR", receipt)
    finally:
        if staged_parent is not None:
            shutil.rmtree(staged_parent, ignore_errors=True)


def _validate_cached_receipt(receipt: object, request_hash: str) -> dict[str, object]:
    if not isinstance(receipt, dict):
        raise PrivilegedGatewayError("replay_ledger_corrupt")
    required_fields = {
        "schema",
        "status",
        "reason",
        "action_id",
        "repository",
        "target",
        "request_hash",
        "mutation_started",
        "mutation_performed",
        "private_evidence_exposed",
        "stderr_exposed",
        "env_exposed",
        "private_paths_exposed",
        "external_side_effects_executed",
        "receipt_hash",
    }
    optional_fields = {
        "expected_main_sha",
        "source_blob",
        "installer_sha256",
        "protected_copy_verified",
        "installed_artifacts_verified",
        "activation_executed",
    }
    if not required_fields <= set(receipt) or set(receipt) - required_fields - optional_fields:
        raise PrivilegedGatewayError("replay_ledger_corrupt")
    if receipt.get("schema") != RECEIPT_SCHEMA_ID or receipt.get("request_hash") != request_hash:
        raise PrivilegedGatewayError("replay_ledger_corrupt")
    if receipt.get("status") not in {"DONE", "NEEDS_OPERATOR"}:
        raise PrivilegedGatewayError("replay_ledger_corrupt")
    if receipt.get("action_id") not in REGISTERED_ACTION_OPERATOR_APPROVALS:
        raise PrivilegedGatewayError("replay_ledger_corrupt")
    if (
        not isinstance(receipt.get("reason"), str)
        or re.fullmatch(r"[A-Z0-9_]{1,80}", receipt["reason"]) is None
    ):
        raise PrivilegedGatewayError("replay_ledger_corrupt")
    if receipt.get("repository") != REPOSITORY or receipt.get("target") != TARGET:
        raise PrivilegedGatewayError("replay_ledger_corrupt")
    if receipt.get("receipt_hash") != _receipt_hash(receipt):
        raise PrivilegedGatewayError("replay_ledger_corrupt")
    if len(json.dumps(receipt, sort_keys=True).encode("utf-8")) > MAX_RECEIPT_BYTES:
        raise PrivilegedGatewayError("replay_ledger_corrupt")
    for bool_key in (
        "mutation_started",
        "mutation_performed",
        "external_side_effects_executed",
    ):
        if not isinstance(receipt.get(bool_key), bool):
            raise PrivilegedGatewayError("replay_ledger_corrupt")
    for leak_key in ("private_evidence_exposed", "stderr_exposed", "env_exposed", "private_paths_exposed"):
        if receipt.get(leak_key) is not False:
            raise PrivilegedGatewayError("replay_ledger_corrupt")
    for optional_hash in ("expected_main_sha", "source_blob"):
        value = receipt.get(optional_hash)
        if value is not None and (
            not isinstance(value, str) or _HEX40_RE.fullmatch(value) is None
        ):
            raise PrivilegedGatewayError("replay_ledger_corrupt")
    value = receipt.get("installer_sha256")
    if value is not None and (
        not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
    ):
        raise PrivilegedGatewayError("replay_ledger_corrupt")
    for optional_bool in ("protected_copy_verified", "installed_artifacts_verified", "activation_executed"):
        value = receipt.get(optional_bool)
        if value is not None and not isinstance(value, bool):
            raise PrivilegedGatewayError("replay_ledger_corrupt")
    return receipt


class _Ledger(AbstractContextManager["_Ledger"]):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")
        self.lock_fd: int | None = None
        self.entries: list[Mapping[str, object]] = []

    def __enter__(self) -> "_Ledger":
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            self.lock_fd = os.open(self.lock_path, flags, 0o600)
            os.fchmod(self.lock_fd, 0o600)
            st = os.fstat(self.lock_fd)
            if not stat.S_ISREG(st.st_mode):
                raise PrivilegedGatewayError("replay_ledger_lock_unsafe")
            if self.path == DEFAULT_REPLAY_LEDGER_PATH and (st.st_uid != 0 or st.st_gid != 0):
                raise PrivilegedGatewayError("replay_ledger_lock_ownership_mismatch")
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX)
            self.entries = self._read_entries()
            return self
        except OSError as exc:
            self.__exit__(None, None, None)
            raise PrivilegedGatewayError("replay_ledger_unavailable") from exc

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.lock_fd is not None:
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(self.lock_fd)
            self.lock_fd = None
        return None

    def _read_entries(self) -> list[Mapping[str, object]]:
        try:
            st = os.lstat(self.path)
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise PrivilegedGatewayError("replay_ledger_unavailable") from exc
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise PrivilegedGatewayError("replay_ledger_unsafe")
        if st.st_size > MAX_LEDGER_BYTES:
            raise PrivilegedGatewayError("replay_ledger_oversize")
        if self.path == DEFAULT_REPLAY_LEDGER_PATH:
            if st.st_uid != 0 or st.st_gid != 0 or stat.S_IMODE(st.st_mode) & 0o077:
                raise PrivilegedGatewayError("replay_ledger_ownership_or_mode_mismatch")
        try:
            text = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise PrivilegedGatewayError("replay_ledger_unavailable") from exc
        entries: list[Mapping[str, object]] = []
        for line in text.splitlines():
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PrivilegedGatewayError("replay_ledger_corrupt") from exc
            if not isinstance(value, Mapping) or set(value) - {
                "kind",
                "request_hash",
                "idempotency_key",
                "action_id",
                "receipt",
            }:
                raise PrivilegedGatewayError("replay_ledger_corrupt")
            entries.append(value)
            if len(entries) > MAX_LEDGER_ENTRIES:
                raise PrivilegedGatewayError("replay_ledger_oversize")
        return entries

    def lookup(self, *, request_hash: str, idempotency_key: str) -> dict[str, object] | None:
        reservation_found = False
        terminal: dict[str, object] | None = None
        for entry in self.entries:
            if entry.get("idempotency_key") != idempotency_key:
                continue
            if entry.get("request_hash") != request_hash:
                raise PrivilegedGatewayError("idempotency_key_conflict")
            if entry.get("kind") == "reservation":
                reservation_found = True
            elif entry.get("kind") == "terminal":
                terminal = _validate_cached_receipt(entry.get("receipt"), request_hash)
            else:
                raise PrivilegedGatewayError("replay_ledger_corrupt")
        if terminal is not None:
            return terminal
        if reservation_found:
            raise PrivilegedGatewayError("prior_execution_state_uncertain")
        return None

    def append(self, entry: Mapping[str, object]) -> None:
        encoded = (json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > MAX_RECEIPT_BYTES:
            raise PrivilegedGatewayError("replay_ledger_entry_oversize")
        try:
            current_size = self.path.stat().st_size if self.path.exists() else 0
            if current_size + len(encoded) > MAX_LEDGER_BYTES:
                raise PrivilegedGatewayError("replay_ledger_oversize")
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(self.path, flags, 0o600)
            try:
                os.fchmod(fd, 0o600)
                st = os.fstat(fd)
                if self.path == DEFAULT_REPLAY_LEDGER_PATH and (st.st_uid != 0 or st.st_gid != 0):
                    raise PrivilegedGatewayError("replay_ledger_ownership_mismatch")
                os.write(fd, encoded)
                os.fsync(fd)
            finally:
                os.close(fd)
        except PrivilegedGatewayError:
            raise
        except OSError as exc:
            raise PrivilegedGatewayError("replay_ledger_unavailable") from exc
        self.entries.append(dict(entry))


def execute_gateway_request(
    request: Mapping[str, object],
    *,
    now: datetime | None = None,
    replay_ledger_path: Path = DEFAULT_REPLAY_LEDGER_PATH,
    registry_path: Path = DEFAULT_ACTION_REGISTRY_PATH,
    capability_registry_path: Path = DEFAULT_CAPABILITY_REGISTRY_PATH,
    checkout_config_path: Path = DEFAULT_CHECKOUT_CONFIG_PATH,
    runner: Callable[[Mapping[str, object]], tuple[int, str]] | None = None,
) -> dict[str, object]:
    mutation_started = False
    mutation_performed = False
    request_hash: str | None = None
    try:
        issued, expires = validate_request_static(
            request,
            registry_path=registry_path,
            capability_registry_path=capability_registry_path,
            checkout_config_path=checkout_config_path,
        )
        request_hash = canonical_request_hash(request)
        with _Ledger(replay_ledger_path) as ledger:
            cached = ledger.lookup(
                request_hash=request_hash,
                idempotency_key=str(request["idempotency_key"]),
            )
            if cached is not None:
                return cached
            current = now or datetime.now(UTC).replace(microsecond=0)
            _validate_new_request_time(issued, expires, current)
            ledger.append(
                {
                    "kind": "reservation",
                    "request_hash": request_hash,
                    "idempotency_key": str(request["idempotency_key"]),
                    "action_id": str(request["action_id"]),
                }
            )
            mutation_started = _action_may_mutate(request["action_id"])
            mutation_performed = mutation_started
            try:
                code, report = (runner or _run_registered_action)(request)
                executor_receipt = _parse_executor_receipt(report)
            except Exception:
                code, executor_receipt = 1, None
            if code != 0 or executor_receipt is None:
                receipt = _public_receipt(
                    "NEEDS_OPERATOR",
                    request,
                    "action_executor_failed",
                    mutation_started=mutation_started,
                    mutation_performed=mutation_performed,
                )
            else:
                status = "DONE" if executor_receipt.get("status") == "DONE" else "NEEDS_OPERATOR"
                receipt = _public_receipt(
                    status,
                    request,
                    str(executor_receipt.get("reason") or "action_reported_blocked"),
                    mutation_started=mutation_started,
                    mutation_performed=mutation_performed,
                    executor_receipt=executor_receipt,
                )
            try:
                ledger.append(
                    {
                        "kind": "terminal",
                        "request_hash": request_hash,
                        "idempotency_key": str(request["idempotency_key"]),
                        "action_id": str(request["action_id"]),
                        "receipt": receipt,
                    }
                )
            except PrivilegedGatewayError:
                return _public_receipt(
                    "NEEDS_OPERATOR",
                    request,
                    "terminal_receipt_persist_failed",
                    mutation_started=mutation_started,
                    mutation_performed=mutation_performed,
                    executor_receipt=executor_receipt,
                )
            return receipt
    except PrivilegedGatewayError as exc:
        return _public_receipt(
            "NEEDS_OPERATOR",
            request if isinstance(request, Mapping) else None,
            exc.reason_code,
            mutation_started=mutation_started,
            mutation_performed=mutation_performed,
        )
    except Exception:
        return _public_receipt(
            "NEEDS_OPERATOR",
            request if isinstance(request, Mapping) else None,
            "gateway_unexpected_failure",
            mutation_started=mutation_started,
            mutation_performed=mutation_performed,
        )


def execute_stdin(stdin: bytes, *, now: datetime | None = None) -> tuple[int, bytes]:
    if len(stdin) > MAX_REQUEST_BYTES:
        receipt = _public_receipt("NEEDS_OPERATOR", None, "request_too_large")
        return 0, (json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8")
    try:
        request = json.loads(stdin.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        receipt = _public_receipt("NEEDS_OPERATOR", None, "request_json_invalid")
        return 0, (json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8")
    if not isinstance(request, Mapping):
        receipt = _public_receipt("NEEDS_OPERATOR", None, "request_json_not_object")
        return 0, (json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8")
    receipt = execute_gateway_request(request, now=now)
    data = (json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8")
    if len(data) > MAX_RECEIPT_BYTES:
        receipt = _public_receipt("NEEDS_OPERATOR", request, "receipt_too_large")
        data = (json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8")
    return 0, data
