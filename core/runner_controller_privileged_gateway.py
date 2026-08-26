from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import stat
from typing import Final

from core.home_edge.esp_lab_stage1_signer_install import (
    HOME_EDGE_ESP_LAB_STAGE1_INSTALLER_BLOB,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_APPROVED_MAIN_SHA,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_BLOB,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_MODE,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_OPERATOR_APPROVAL,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PAYLOAD_BLOB,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PROTECTED_INSTALLER,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_SOURCE_PATH,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_SUDOERS_SHA256,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_TRUSTED_SOURCE_ANCESTOR_SHA,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_WRAPPER_BLOB,
)


REQUEST_SCHEMA_ID: Final = "skeleton.runner_controller_privileged_request.v1"
RECEIPT_SCHEMA_ID: Final = "skeleton.runner_controller_privileged_receipt.v1"
REPOSITORY: Final = "alanua/Skeleton"
TARGET: Final = "runner-controller"
ROOT: Final = Path(__file__).resolve().parents[1]
INSTALL_ROOT: Final = Path("/usr/local/lib/skeleton/runner-controller")
CONFIG_ROOT: Final = INSTALL_ROOT / "config"
DEFAULT_ACTION_REGISTRY_PATH: Final = (
    ROOT / "config/RUNNER_PRIVILEGED_ACTIONS.yaml"
    if (ROOT / "config/RUNNER_PRIVILEGED_ACTIONS.yaml").exists()
    else CONFIG_ROOT / "RUNNER_PRIVILEGED_ACTIONS.yaml"
    if (CONFIG_ROOT / "RUNNER_PRIVILEGED_ACTIONS.yaml").exists()
    else ROOT / "RUNNER_PRIVILEGED_ACTIONS.yaml"
)
DEFAULT_CAPABILITY_REGISTRY_PATH: Final = (
    ROOT / "config/CAPABILITY_REGISTRY.yaml"
    if (ROOT / "config/CAPABILITY_REGISTRY.yaml").exists()
    else CONFIG_ROOT / "CAPABILITY_REGISTRY.yaml"
    if (CONFIG_ROOT / "CAPABILITY_REGISTRY.yaml").exists()
    else ROOT / "CAPABILITY_REGISTRY.yaml"
)
DEFAULT_CHECKOUT_CONFIG_PATH: Final = (
    ROOT / "config/checkout.json"
    if (ROOT / "config/checkout.json").exists()
    else CONFIG_ROOT / "checkout.json"
    if (CONFIG_ROOT / "checkout.json").exists()
    else ROOT / "config/runner_controller_privileged_checkout.json"
)
DEFAULT_REPLAY_LEDGER_PATH: Final = Path(
    "/var/lib/skeleton/runner-controller/privileged-gateway-ledger.jsonl"
)
GATEWAY_INSTALL_PATH: Final = Path(
    "/usr/local/libexec/skeleton/runner-controller/privileged-gateway"
)
LOCAL_SUDO_GATEWAY_ARGV: Final = (
    "/usr/bin/sudo",
    "-n",
    str(GATEWAY_INSTALL_PATH),
)
FORCED_COMMAND_ARGV: Final = (
    str(GATEWAY_INSTALL_PATH),
    "--forced-command",
)
SSH_GATEWAY_USER: Final = "skeleton-runner-gateway"
CANONICAL_CHECKOUT_PATH: Final = Path("/home/agent/agent-dev/repos/Skeleton")
MAX_REQUEST_BYTES: Final = 16 * 1024
MAX_TOKEN_BYTES: Final = 160
MAX_PATH_BYTES: Final = 4096
MAX_RECEIPT_BYTES: Final = 16 * 1024
MAX_AGE_SECONDS: Final = 300
REQUEST_FIELDS: Final = (
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
)
_HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class PrivilegedGatewayError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = _public_reason(reason_code)


@dataclass(frozen=True)
class GatewayAction:
    action_id: str
    handler: str
    repository: str
    target: str
    operator_approval: str
    source_path: str
    source_blob: str
    source_mode: str
    trusted_source_ancestor_sha: str
    destination: str
    installer_argv: tuple[str, ...]
    post_audit_artifacts: tuple[tuple[str, str, int], ...]


def _public_reason(reason: str) -> str:
    return re.sub(r"[^A-Z0-9_]+", "_", reason.upper()).strip("_")[:80] or "BLOCKED"


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _parse_utc(value: object, field: str) -> datetime:
    if not isinstance(value, str) or _ISO_Z_RE.fullmatch(value) is None:
        raise PrivilegedGatewayError(f"{field}_invalid")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def canonical_json_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_request_hash(request: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(request)).hexdigest()


def _yaml_scalar(value: str) -> object:
    value = value.strip()
    if value == "":
        return ""
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "Null", "~"}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value


def _split_yaml_key_value(text: str) -> tuple[str, str]:
    if ":" not in text:
        raise PrivilegedGatewayError("yaml_subset_invalid")
    key, value = text.split(":", 1)
    key = key.strip()
    if not key:
        raise PrivilegedGatewayError("yaml_subset_invalid")
    return key, value.strip()


def _parse_yaml_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[object, int]:
    if index >= len(lines):
        return {}, index
    if lines[index][0] < indent:
        return {}, index
    is_list = lines[index][1].startswith("- ")
    if is_list:
        values: list[object] = []
        while index < len(lines) and lines[index][0] == indent and lines[index][1].startswith("- "):
            item = lines[index][1][2:].strip()
            index += 1
            if not item:
                child, index = _parse_yaml_block(lines, index, indent + 2)
                values.append(child)
                continue
            if ":" in item:
                key, raw_value = _split_yaml_key_value(item)
                item_mapping: dict[str, object] = {}
                if raw_value:
                    item_mapping[key] = _yaml_scalar(raw_value)
                elif index < len(lines) and lines[index][0] >= indent + 2:
                    item_mapping[key], index = _parse_yaml_block(lines, index, lines[index][0])
                else:
                    item_mapping[key] = {}
                if index < len(lines) and lines[index][0] >= indent + 2:
                    child, index = _parse_yaml_block(lines, index, lines[index][0])
                    if isinstance(child, Mapping):
                        item_mapping.update(child)
                    else:
                        raise PrivilegedGatewayError("yaml_subset_invalid")
                values.append(item_mapping)
            else:
                values.append(_yaml_scalar(item))
        return values, index

    values: dict[str, object] = {}
    while index < len(lines) and lines[index][0] == indent and not lines[index][1].startswith("- "):
        key, raw_value = _split_yaml_key_value(lines[index][1])
        index += 1
        if raw_value:
            values[key] = _yaml_scalar(raw_value)
        elif index < len(lines) and lines[index][0] >= indent:
            child_indent = lines[index][0]
            values[key], index = _parse_yaml_block(lines, index, child_indent)
        else:
            values[key] = {}
    return values, index


def _load_yaml_subset(path: Path) -> Mapping[str, object]:
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PrivilegedGatewayError("yaml_trust_anchor_unavailable") from exc
    lines: list[tuple[int, str]] = []
    for raw in raw_lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        stripped = raw.strip()
        if ":" not in stripped and not stripped.startswith("- "):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append((indent, stripped))
    parsed, index = _parse_yaml_block(lines, 0, lines[0][0] if lines else 0)
    if index != len(lines) or not isinstance(parsed, Mapping):
        raise PrivilegedGatewayError("yaml_subset_invalid")
    return parsed


def _load_gateway_capability(path: Path) -> Mapping[str, object] | None:
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PrivilegedGatewayError("capability_registry_unavailable") from exc
    start: int | None = None
    for index, raw in enumerate(raw_lines):
        if raw == "  runner_controller_privileged_gateway:":
            start = index + 1
            break
    if start is None:
        return None
    block: list[str] = []
    for raw in raw_lines[start:]:
        if raw.startswith("  ") and not raw.startswith("    ") and raw.strip().endswith(":"):
            break
        if raw.startswith("    ") or raw.startswith("    -"):
            block.append(raw[2:])
    if not block:
        raise PrivilegedGatewayError("capability_registry_gateway_invalid")
    temp_lines: list[tuple[int, str]] = []
    for raw in block:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        stripped = raw.strip()
        if ":" not in stripped and not stripped.startswith("- "):
            continue
        temp_lines.append((len(raw) - len(raw.lstrip(" ")), stripped))
    parsed, index = _parse_yaml_block(temp_lines, 0, temp_lines[0][0] if temp_lines else 0)
    if index != len(temp_lines) or not isinstance(parsed, Mapping):
        raise PrivilegedGatewayError("capability_registry_gateway_invalid")
    return parsed


def _verify_root_owned_trust_anchor(path: Path) -> None:
    if not str(path).startswith("/usr/local/lib/skeleton/runner-controller/"):
        return
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise PrivilegedGatewayError("trust_anchor_unavailable") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise PrivilegedGatewayError("trust_anchor_not_regular")
    if st.st_uid != 0 or st.st_gid != 0:
        raise PrivilegedGatewayError("trust_anchor_ownership_mismatch")
    if stat.S_IMODE(st.st_mode) & 0o022:
        raise PrivilegedGatewayError("trust_anchor_writable")


def load_checkout_config(path: Path = DEFAULT_CHECKOUT_CONFIG_PATH) -> Mapping[str, object]:
    if not path.exists():
        return {
            "schema": "skeleton.runner_controller_checkout_config.v1",
            "repository": REPOSITORY,
            "checkout_path": str(CANONICAL_CHECKOUT_PATH),
        }
    _verify_root_owned_trust_anchor(path)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PrivilegedGatewayError("checkout_config_unavailable") from exc
    if (
        not isinstance(loaded, Mapping)
        or loaded.get("schema") != "skeleton.runner_controller_checkout_config.v1"
        or loaded.get("repository") != REPOSITORY
        or loaded.get("checkout_path") != str(CANONICAL_CHECKOUT_PATH)
        or set(loaded) != {"schema", "repository", "checkout_path"}
    ):
        raise PrivilegedGatewayError("checkout_config_unapproved")
    return loaded


def build_gateway_request(
    *,
    request_id: str,
    idempotency_key: str,
    expected_main_sha: str,
    registered_clean_main_sha: str,
    github_main_sha: str,
    checkout_path: Path,
    checkout_head_sha: str,
    checkout_origin_main_sha: str,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> dict[str, object]:
    issued = issued_at or _utc_now()
    expires = expires_at or issued + timedelta(seconds=MAX_AGE_SECONDS)
    return {
        "schema": REQUEST_SCHEMA_ID,
        "request_id": request_id,
        "idempotency_key": idempotency_key,
        "action_id": HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID,
        "issued_at": issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repository": REPOSITORY,
        "target": TARGET,
        "operator_approval": HOME_EDGE_ESP_LAB_STAGE1_SIGNER_OPERATOR_APPROVAL,
        "expected_main_sha": expected_main_sha,
        "registered_clean_main_sha": registered_clean_main_sha,
        "github_main_sha": github_main_sha,
        "checkout_path": str(checkout_path),
        "checkout_head_sha": checkout_head_sha,
        "checkout_origin_main_sha": checkout_origin_main_sha,
    }


def load_action_registry(path: Path = DEFAULT_ACTION_REGISTRY_PATH) -> dict[str, GatewayAction]:
    _verify_root_owned_trust_anchor(path)
    loaded = _load_yaml_subset(path)
    if not isinstance(loaded, Mapping) or loaded.get("schema") != "skeleton.runner_privileged_actions.v1":
        raise PrivilegedGatewayError("action_registry_schema_mismatch")
    actions = loaded.get("actions")
    if not isinstance(actions, list) or len(actions) != 1:
        raise PrivilegedGatewayError("action_registry_action_set_mismatch")
    action = _action_from_mapping(actions[0])
    _assert_initial_esp_signer_action(action)
    return {action.action_id: action}


def _action_from_mapping(raw: object) -> GatewayAction:
    if not isinstance(raw, Mapping):
        raise PrivilegedGatewayError("action_registry_entry_invalid")
    required = {
        "action_id",
        "handler",
        "repository",
        "target",
        "operator_approval",
        "source_path",
        "source_blob",
        "source_mode",
        "trusted_source_ancestor_sha",
        "destination",
        "installer_argv",
        "post_audit_artifacts",
    }
    if set(raw) != required:
        raise PrivilegedGatewayError("action_registry_field_set_mismatch")
    argv = raw["installer_argv"]
    artifacts = raw["post_audit_artifacts"]
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise PrivilegedGatewayError("action_registry_argv_invalid")
    if not isinstance(artifacts, list):
        raise PrivilegedGatewayError("action_registry_post_audit_invalid")
    parsed_artifacts: list[tuple[str, str, int]] = []
    for artifact in artifacts:
        if (
            not isinstance(artifact, Mapping)
            or set(artifact) != {"path", "content_hash", "mode"}
            or not isinstance(artifact["path"], str)
            or not isinstance(artifact["content_hash"], str)
            or not isinstance(artifact["mode"], str)
        ):
            raise PrivilegedGatewayError("action_registry_post_audit_invalid")
        parsed_artifacts.append(
            (artifact["path"], artifact["content_hash"], int(artifact["mode"], 8))
        )
    return GatewayAction(
        action_id=str(raw["action_id"]),
        handler=str(raw["handler"]),
        repository=str(raw["repository"]),
        target=str(raw["target"]),
        operator_approval=str(raw["operator_approval"]),
        source_path=str(raw["source_path"]),
        source_blob=str(raw["source_blob"]),
        source_mode=str(raw["source_mode"]),
        trusted_source_ancestor_sha=str(raw["trusted_source_ancestor_sha"]),
        destination=str(raw["destination"]),
        installer_argv=tuple(argv),
        post_audit_artifacts=tuple(parsed_artifacts),
    )


def _assert_initial_esp_signer_action(action: GatewayAction) -> None:
    expected_artifacts = (
        (
            "/usr/local/libexec/skeleton/home-edge/esp-lab-stage1/signer",
            HOME_EDGE_ESP_LAB_STAGE1_SIGNER_WRAPPER_BLOB,
            0o555,
        ),
        (
            "/usr/local/lib/skeleton/home-edge/esp-lab-stage1/signer_payload.py",
            HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PAYLOAD_BLOB,
            0o555,
        ),
        (
            "/usr/local/lib/skeleton/home-edge/esp-lab-stage1/install_home_edge_esp_lab.sh",
            HOME_EDGE_ESP_LAB_STAGE1_INSTALLER_BLOB,
            0o444,
        ),
        (
            "/etc/sudoers.d/skeleton-home-edge-esp-lab-stage1-signer",
            HOME_EDGE_ESP_LAB_STAGE1_SIGNER_SUDOERS_SHA256,
            0o440,
        ),
    )
    if (
        action.action_id != HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID
        or action.handler != "home_edge_esp_lab_stage1_signer_install"
        or action.repository != REPOSITORY
        or action.target != TARGET
        or action.operator_approval != HOME_EDGE_ESP_LAB_STAGE1_SIGNER_OPERATOR_APPROVAL
        or action.source_path != HOME_EDGE_ESP_LAB_STAGE1_SIGNER_SOURCE_PATH
        or action.source_blob != HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_BLOB
        or action.source_mode != HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_MODE
        or action.trusted_source_ancestor_sha
        != HOME_EDGE_ESP_LAB_STAGE1_SIGNER_TRUSTED_SOURCE_ANCESTOR_SHA
        or action.destination != str(HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PROTECTED_INSTALLER)
        or action.installer_argv
        != (str(HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PROTECTED_INSTALLER), "--repo-root", "{checkout_path}")
        or action.post_audit_artifacts != expected_artifacts
    ):
        raise PrivilegedGatewayError("initial_esp_signer_action_drift")


def verify_protected_capability_metadata(path: Path = DEFAULT_CAPABILITY_REGISTRY_PATH) -> None:
    _verify_root_owned_trust_anchor(path)
    capability = _load_gateway_capability(path)
    if capability is None:
        if path == ROOT / "CAPABILITY_REGISTRY.yaml":
            return
        raise PrivilegedGatewayError("capability_registry_gateway_missing")
    if not isinstance(capability, Mapping):
        raise PrivilegedGatewayError("capability_registry_gateway_invalid")
    required = {
        "core/runner_controller_privileged_gateway.py",
        "core/home_edge/esp_lab_stage1_signer_install.py",
        "scripts/runner_controller_privileged_gateway.py",
        "scripts/install_runner_controller_privileged_gateway.sh",
        "RUNNER_PRIVILEGED_ACTIONS.yaml",
        "schemas/runner_controller_privileged_request.schema.json",
        "schemas/runner_controller_privileged_receipt.schema.json",
        "docs/RUNNER_CONTROLLER_PRIVILEGED_GATEWAY.md",
    }
    if (
        capability.get("status") != "available"
        or capability.get("live_runtime_execution") is not True
        or capability.get("protected") is not True
        or not required <= set(capability.get("requires", []))
    ):
        raise PrivilegedGatewayError("capability_registry_gateway_unapproved")


def validate_gateway_request(
    request: Mapping[str, object],
    *,
    now: datetime | None = None,
    registry_path: Path = DEFAULT_ACTION_REGISTRY_PATH,
    capability_registry_path: Path = DEFAULT_CAPABILITY_REGISTRY_PATH,
    checkout_config_path: Path = DEFAULT_CHECKOUT_CONFIG_PATH,
) -> GatewayAction:
    if set(request) != set(REQUEST_FIELDS):
        raise PrivilegedGatewayError("request_field_set_mismatch")
    if request["schema"] != REQUEST_SCHEMA_ID:
        raise PrivilegedGatewayError("request_schema_mismatch")
    for field in ("request_id", "idempotency_key", "action_id"):
        value = request[field]
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
        if not isinstance(request[field], str) or _HEX40_RE.fullmatch(str(request[field])) is None:
            raise PrivilegedGatewayError(f"{field}_invalid")
    checkout_path = request["checkout_path"]
    if (
        not isinstance(checkout_path, str)
        or not checkout_path.startswith("/home/agent/agent-dev/")
        or len(checkout_path.encode("utf-8")) > MAX_PATH_BYTES
        or "\x00" in checkout_path
    ):
        raise PrivilegedGatewayError("checkout_path_invalid")
    checkout_config = load_checkout_config(checkout_config_path)
    if checkout_path != checkout_config.get("checkout_path"):
        raise PrivilegedGatewayError("checkout_path_not_canonical")
    issued = _parse_utc(request["issued_at"], "issued_at")
    expires = _parse_utc(request["expires_at"], "expires_at")
    current = now or _utc_now()
    if expires <= issued or (expires - issued).total_seconds() > MAX_AGE_SECONDS:
        raise PrivilegedGatewayError("request_expiry_window_invalid")
    if issued > current or expires < current:
        raise PrivilegedGatewayError("request_expired_or_not_yet_valid")
    if request["repository"] != REPOSITORY or request["target"] != TARGET:
        raise PrivilegedGatewayError("request_authority_mismatch")
    verify_protected_capability_metadata(capability_registry_path)
    actions = load_action_registry(registry_path)
    action = actions.get(str(request["action_id"]))
    if action is None:
        raise PrivilegedGatewayError("action_not_registered")
    if request["operator_approval"] != action.operator_approval:
        raise PrivilegedGatewayError("operator_approval_mismatch")
    expected_sha = request["expected_main_sha"]
    if (
        request["registered_clean_main_sha"] != expected_sha
        or request["github_main_sha"] != expected_sha
        or request["checkout_head_sha"] != expected_sha
        or request["checkout_origin_main_sha"] != expected_sha
    ):
        raise PrivilegedGatewayError("request_sha_mismatch")
    return action


def _read_ledger_entries(path: Path) -> list[Mapping[str, object]]:
    try:
        data = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise PrivilegedGatewayError("replay_ledger_unavailable") from exc
    entries: list[Mapping[str, object]] = []
    for line in data.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PrivilegedGatewayError("replay_ledger_corrupt") from exc
        if not isinstance(parsed, Mapping):
            raise PrivilegedGatewayError("replay_ledger_corrupt")
        entries.append(parsed)
    return entries


def _reserve_ledger_entry(
    *,
    path: Path,
    request: Mapping[str, object],
    request_hash: str,
    now: datetime,
) -> None:
    if path is None:
        return
    idempotency_key = str(request["idempotency_key"])
    lock = path.with_suffix(path.suffix + ".lock")
    try:
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise PrivilegedGatewayError("replay_ledger_unavailable") from exc
    try:
        os.mkdir(lock, 0o700)
    except FileExistsError as exc:
        raise PrivilegedGatewayError("replay_ledger_locked") from exc
    except OSError as exc:
        raise PrivilegedGatewayError("replay_ledger_unavailable") from exc
    try:
        for entry in _read_ledger_entries(path):
            if entry.get("request_hash") == request_hash:
                raise PrivilegedGatewayError("request_replay")
            if entry.get("idempotency_key") == idempotency_key:
                raise PrivilegedGatewayError("idempotency_key_replay")
        record = {
            "schema": "skeleton.runner_controller_privileged_replay_ledger_entry.v1",
            "recorded_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "request_hash": request_hash,
            "idempotency_key": idempotency_key,
            "action_id": str(request["action_id"]),
        }
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        fd = os.open(path, flags, 0o600)
        try:
            os.write(fd, canonical_json_bytes(record) + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        try:
            os.rmdir(lock)
        except OSError:
            pass


def _parse_executor_receipt(report: str) -> Mapping[str, object] | None:
    marker = "Receipt:\n"
    if marker not in report:
        return None
    try:
        parsed = json.loads(report.split(marker, 1)[1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _public_receipt(
    status: str,
    request: Mapping[str, object] | None,
    reason: str,
    *,
    executor_receipt: Mapping[str, object] | None = None,
) -> dict[str, object]:
    action_id = str(request.get("action_id")) if request is not None else ""
    request_hash = canonical_request_hash(request) if request is not None else None
    receipt: dict[str, object] = {
        "schema": RECEIPT_SCHEMA_ID,
        "status": status,
        "reason": _public_reason(reason),
        "action_id": action_id,
        "repository": REPOSITORY,
        "target": TARGET,
        "request_hash": request_hash,
        "private_evidence_exposed": False,
        "stderr_exposed": False,
        "env_exposed": False,
        "private_paths_exposed": False,
        "external_side_effects_executed": (
            executor_receipt is not None
            and (
                executor_receipt.get("protected_copy_verified") is True
                or executor_receipt.get("installed_artifacts_verified") is True
                or executor_receipt.get("activation_executed") is True
            )
        ),
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
            if key in executor_receipt:
                receipt[key] = executor_receipt[key]
    receipt["receipt_hash"] = hashlib.sha256(
        json.dumps({k: v for k, v in receipt.items() if k != "receipt_hash"}, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return receipt


def execute_gateway_request(
    request: Mapping[str, object],
    *,
    now: datetime | None = None,
    seen_request_hashes: set[str] | None = None,
    replay_ledger_path: Path | None = None,
    registry_path: Path = DEFAULT_ACTION_REGISTRY_PATH,
    capability_registry_path: Path = DEFAULT_CAPABILITY_REGISTRY_PATH,
    checkout_config_path: Path = DEFAULT_CHECKOUT_CONFIG_PATH,
    runner: Callable[[Mapping[str, object], GatewayAction], tuple[int, str]] | None = None,
) -> dict[str, object]:
    try:
        current = now or _utc_now()
        action = validate_gateway_request(
            request,
            now=current,
            registry_path=registry_path,
            capability_registry_path=capability_registry_path,
            checkout_config_path=checkout_config_path,
        )
        request_hash = canonical_request_hash(request)
        if seen_request_hashes is not None:
            if request_hash in seen_request_hashes:
                raise PrivilegedGatewayError("request_replay")
            seen_request_hashes.add(request_hash)
        if replay_ledger_path is not None:
            _reserve_ledger_entry(
                path=replay_ledger_path,
                request=request,
                request_hash=request_hash,
                now=current,
            )
        code, report = (runner or _run_registered_action)(request, action)
        executor_receipt = _parse_executor_receipt(report)
        if code != 0 or executor_receipt is None:
            return _public_receipt("NEEDS_OPERATOR", request, "action_executor_failed", executor_receipt=None)
        status = "DONE" if executor_receipt.get("status") == "DONE" else "NEEDS_OPERATOR"
        return _public_receipt(
            status,
            request,
            str(executor_receipt.get("reason") or "ACTION_REPORTED_BLOCKED"),
            executor_receipt=executor_receipt,
        )
    except PrivilegedGatewayError as exc:
        return _public_receipt("NEEDS_OPERATOR", request if isinstance(request, Mapping) else None, exc.reason_code)
    except Exception:
        return _public_receipt("NEEDS_OPERATOR", request if isinstance(request, Mapping) else None, "gateway_unexpected_failure")


def _run_registered_action(
    request: Mapping[str, object],
    action: GatewayAction,
) -> tuple[int, str]:
    if action.action_id != HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID:
        raise PrivilegedGatewayError("action_handler_missing")
    from core.home_edge.esp_lab_stage1_signer_install import (
        execute_home_edge_esp_lab_stage1_signer_install,
    )

    return execute_home_edge_esp_lab_stage1_signer_install(
        expected_main_sha=str(request["expected_main_sha"]),
        registered_clean_main_sha=str(request["registered_clean_main_sha"]),
        github_main_sha=str(request["github_main_sha"]),
        checkout_path=Path(str(request["checkout_path"])),
        checkout_head_sha=str(request["checkout_head_sha"]),
        checkout_origin_main_sha=str(request["checkout_origin_main_sha"]),
        protected_run_command=_root_local_protected_run,
    )


def _root_local_protected_run(argv: list[str], timeout: int | None) -> tuple[int, str]:
    if argv[:2] != ["/usr/bin/sudo", "-n"]:
        raise PrivilegedGatewayError("nested_privilege_argv_invalid")
    result = subprocess.run(
        argv[2:],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout or 60,
        check=False,
    )
    return result.returncode, "\n".join(part for part in (result.stdout, result.stderr) if part)


@dataclass(frozen=True)
class LocalSudoGatewayTransport:
    argv: tuple[str, ...] = LOCAL_SUDO_GATEWAY_ARGV
    run_command: Callable[[tuple[str, ...], bytes, int], tuple[int, bytes]] | None = None
    timeout_seconds: int = 180

    def canonical_request(self, request: Mapping[str, object]) -> bytes:
        validate_gateway_request(request)
        return canonical_json_bytes(request)

    def submit(self, request: Mapping[str, object]) -> tuple[int, bytes]:
        payload = self.canonical_request(request)
        if self.run_command is not None:
            return self.run_command(self.argv, payload, self.timeout_seconds)
        completed = subprocess.run(
            list(self.argv),
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.timeout_seconds,
            check=False,
        )
        return completed.returncode, completed.stdout


@dataclass(frozen=True)
class SshForcedCommandGatewayTransport:
    forced_command_argv: tuple[str, ...] = FORCED_COMMAND_ARGV

    def canonical_request(self, request: Mapping[str, object]) -> bytes:
        validate_gateway_request(request)
        return canonical_json_bytes(request)

    def authorized_keys_line(self, public_key: str) -> str:
        key = public_key.strip()
        if not key.startswith(("ssh-ed25519 ", "ecdsa-sha2-nistp256 ")):
            raise PrivilegedGatewayError("ssh_public_key_type_unapproved")
        command = " ".join(self.forced_command_argv)
        options = (
            f'command="{command}"',
            "no-pty",
            "no-port-forwarding",
            "no-X11-forwarding",
            "no-agent-forwarding",
            "no-user-rc",
        )
        return ",".join(options) + " " + key


def deterministic_sshd_config_fragment() -> str:
    return "\n".join(
        (
            f"Match User {SSH_GATEWAY_USER}",
            "    PasswordAuthentication no",
            "    KbdInteractiveAuthentication no",
            "    PermitTTY no",
            "    AllowTcpForwarding no",
            "    X11Forwarding no",
            "    AllowAgentForwarding no",
            f"    ForceCommand {' '.join(FORCED_COMMAND_ARGV)}",
            "",
        )
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
    receipt = execute_gateway_request(
        request,
        now=now,
        replay_ledger_path=DEFAULT_REPLAY_LEDGER_PATH,
    )
    data = (json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8")
    if len(data) > MAX_RECEIPT_BYTES:
        receipt = _public_receipt("NEEDS_OPERATOR", request if isinstance(request, Mapping) else None, "receipt_too_large")
        data = (json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8")
    return 0, data
