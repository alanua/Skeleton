from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Final

import yaml

from core.runner_repository_maintenance_executor import (
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_APPROVED_MAIN_SHA,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_BLOB,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_MODE,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_OPERATOR_APPROVAL,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PROTECTED_INSTALLER,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_SOURCE_PATH,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_TRUSTED_SOURCE_ANCESTOR_SHA,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_WRAPPER_BLOB,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PAYLOAD_BLOB,
    HOME_EDGE_ESP_LAB_STAGE1_INSTALLER_BLOB,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_SUDOERS_SHA256,
    execute_home_edge_esp_lab_stage1_signer_install,
)


REQUEST_SCHEMA_ID: Final = "skeleton.runner_controller_privileged_request.v1"
RECEIPT_SCHEMA_ID: Final = "skeleton.runner_controller_privileged_receipt.v1"
REPOSITORY: Final = "alanua/Skeleton"
TARGET: Final = "runner-controller"
ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_ACTION_REGISTRY_PATH: Final = ROOT / "RUNNER_PRIVILEGED_ACTIONS.yaml"
DEFAULT_CAPABILITY_REGISTRY_PATH: Final = ROOT / "CAPABILITY_REGISTRY.yaml"
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
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PrivilegedGatewayError("action_registry_unavailable") from exc
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
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PrivilegedGatewayError("capability_registry_unavailable") from exc
    capability = (
        loaded.get("capabilities", {}).get("runner_controller_privileged_gateway")
        if isinstance(loaded, Mapping)
        else None
    )
    if capability is None:
        return
    if not isinstance(capability, Mapping):
        raise PrivilegedGatewayError("capability_registry_gateway_invalid")
    required = {
        "core/runner_controller_privileged_gateway.py",
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
        "external_side_effects_executed": status == "DONE",
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
    registry_path: Path = DEFAULT_ACTION_REGISTRY_PATH,
    capability_registry_path: Path = DEFAULT_CAPABILITY_REGISTRY_PATH,
    runner: Callable[[Mapping[str, object], GatewayAction], tuple[int, str]] | None = None,
) -> dict[str, object]:
    try:
        action = validate_gateway_request(
            request,
            now=now,
            registry_path=registry_path,
            capability_registry_path=capability_registry_path,
        )
        request_hash = canonical_request_hash(request)
        if seen_request_hashes is not None:
            if request_hash in seen_request_hashes:
                raise PrivilegedGatewayError("request_replay")
            seen_request_hashes.add(request_hash)
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
            "PermitRootLogin no",
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
    receipt = execute_gateway_request(request, now=now)
    data = (json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8")
    if len(data) > MAX_RECEIPT_BYTES:
        receipt = _public_receipt("NEEDS_OPERATOR", request if isinstance(request, Mapping) else None, "receipt_too_large")
        data = (json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8")
    return 0, data
