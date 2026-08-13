from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping
from uuid import uuid4

from core.home_edge.executor import HomeEdgeExecError, HomeEdgeExecRequest, sign_request
from core.home_edge.executor_gateway import EXEC_HMAC_SECRET_ENV, execute_home_edge_request


TASK_ID = "home_edge_01_display_power_off_v1"
REPOSITORY = "alanua/Skeleton"
TARGET_NODE = "home-edge-01"
OPERATOR_APPROVAL = "EXPLICIT_2026_08_09_TURN_OFF_HOME_EDGE_MONITOR"
APPROVAL_REF = OPERATOR_APPROVAL
IDEMPOTENCY_KEY = "home-edge-01-display-power-off-v1"
REQUEST_TIMEOUT_SECONDS = 20
RECEIPT_SCHEMA = "skeleton.home_edge.display_power_off_receipt.v1"
EXPECTED_MAIN_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
FIELD_RE = re.compile(r"^\s*(?P<field>[A-Za-z][A-Za-z0-9 _-]{0,80}):\s*(?P<value>.*?)\s*$")
PUBLIC_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:=-]+$")
MAX_EXECUTOR_STDOUT_BYTES = 65536

RECEIPT_FIELDS = (
    "maintenance_task_id",
    "request_accepted",
    "applied",
    "physically_verified",
    "display_power_status",
    "idempotency_status",
    "executor_receipt_hash",
    "audit_receipt_hash",
    "stable_reason",
    "success_criteria",
)

ALLOWED_FIELDS = frozenset(
    {
        "Mode",
        "Maintenance Task ID",
        "Repository",
        "Expected Main SHA",
        "Operator Approval",
        "Target",
    }
)


@dataclass(frozen=True)
class RuntimeInput:
    repository: str
    expected_main_sha: str
    operator_approval: str
    target: str


def execute_display_power_off_task(
    body: str,
    *,
    registered_clean_main_sha: str,
    github_main_sha: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    runtime_input = parse_runtime_input(body)
    validate_main_sha(
        runtime_input.expected_main_sha,
        registered_clean_main_sha=registered_clean_main_sha,
        github_main_sha=github_main_sha,
    )
    request = build_display_power_off_request(environment=environment)
    try:
        executor_receipt = execute_home_edge_request(request.to_mapping())
    except (subprocess.TimeoutExpired, TimeoutError):
        return _blocked_receipt("executor_transport_timeout")
    except HomeEdgeExecError:
        return _blocked_receipt("executor_transport_failed")
    except Exception:
        return _blocked_receipt("executor_transport_exception")
    public = public_receipt_from_executor_stdout(executor_receipt.to_mapping())
    public["executor_receipt_hash"] = executor_receipt.receipt_hash
    public["idempotency_status"] = _public_idempotency_status(
        executor_receipt.to_mapping().get("idempotency")
    )
    public["audit_receipt_hash"] = _audit_hash(public)
    if not success_criteria_met(public):
        public["success_criteria"] = "not_met"
        public["audit_receipt_hash"] = _audit_hash(public)
    return public


def parse_runtime_input(body: str) -> RuntimeInput:
    fields: dict[str, str] = {}
    duplicates: set[str] = set()
    for line in _metadata_lines(body):
        match = FIELD_RE.match(line)
        if match is None:
            continue
        field = match.group("field").strip()
        value = match.group("value").strip()
        if not value:
            continue
        if field in fields:
            duplicates.add(field)
        fields[field] = value
    if duplicates:
        raise ValueError("duplicate_runtime_input_field")
    unknown = sorted(set(fields) - ALLOWED_FIELDS)
    if unknown:
        raise ValueError("unknown_runtime_input_field")
    if fields.get("Mode") != "RUNTIME_MAINTENANCE_TASK":
        raise ValueError("runtime_mode_mismatch")
    if fields.get("Maintenance Task ID") != TASK_ID:
        raise ValueError("maintenance_task_id_mismatch")
    runtime_input = RuntimeInput(
        repository=fields.get("Repository", ""),
        expected_main_sha=fields.get("Expected Main SHA", ""),
        operator_approval=fields.get("Operator Approval", ""),
        target=fields.get("Target", ""),
    )
    if runtime_input.repository != REPOSITORY:
        raise ValueError("repository_mismatch")
    if EXPECTED_MAIN_SHA_RE.fullmatch(runtime_input.expected_main_sha) is None:
        raise ValueError("expected_main_sha_malformed")
    if runtime_input.operator_approval != OPERATOR_APPROVAL:
        raise ValueError("operator_approval_mismatch")
    if runtime_input.target != TARGET_NODE:
        raise ValueError("target_mismatch")
    return runtime_input


def validate_main_sha(
    expected_main_sha: str,
    *,
    registered_clean_main_sha: str,
    github_main_sha: str,
) -> None:
    if EXPECTED_MAIN_SHA_RE.fullmatch(registered_clean_main_sha or "") is None:
        raise ValueError("registered_clean_main_sha_unavailable")
    if EXPECTED_MAIN_SHA_RE.fullmatch(github_main_sha or "") is None:
        raise ValueError("github_main_sha_unavailable")
    if expected_main_sha != registered_clean_main_sha:
        raise ValueError("registered_clean_main_sha_mismatch")
    if expected_main_sha != github_main_sha:
        raise ValueError("github_main_sha_mismatch")


def build_display_power_off_request(
    *, environment: Mapping[str, str] | None = None
) -> HomeEdgeExecRequest:
    env = os.environ if environment is None else environment
    secret = env.get(EXEC_HMAC_SECRET_ENV, "")
    if not secret:
        raise ValueError("home_edge_exec_hmac_secret_missing")
    request = HomeEdgeExecRequest.from_mapping(
        {
            "request_id": f"{TASK_ID}-{uuid4()}",
            "node_id": TARGET_NODE,
            "execution_lane": "routine_mutation",
            "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "operator_approval_ref": APPROVAL_REF,
            "idempotency_key": IDEMPOTENCY_KEY,
            "run_as": "desktop-user",
            "mode": "script",
            "script": DISPLAY_POWER_OFF_SCRIPT,
            "script_interpreter": "bash",
            "timestamp": datetime.now(UTC).isoformat(),
            "nonce": f"{TASK_ID}-{uuid4()}",
            "max_output_bytes": MAX_EXECUTOR_STDOUT_BYTES,
        }
    )
    signature = sign_request(request, secret)
    return HomeEdgeExecRequest.from_mapping(
        {**request.to_mapping(include_signature=False), "signature": signature}
    )


def public_receipt_from_executor_stdout(receipt: Mapping[str, Any]) -> dict[str, object]:
    status = receipt.get("status")
    if status not in {"ok", "failed"}:
        return _blocked_receipt("executor_receipt_not_ok")
    public = _public_receipt_from_stdout(receipt.get("stdout"))
    if public is not None:
        if status == "failed" or receipt.get("exit_code") != 0:
            public["success_criteria"] = "not_met"
            public["audit_receipt_hash"] = _audit_hash(public)
        return public
    if status == "failed" or receipt.get("exit_code") != 0:
        return _blocked_receipt("executor_receipt_not_ok")
    return _blocked_receipt("executor_stdout_not_json")


def _public_receipt_from_stdout(stdout: Any) -> dict[str, object] | None:
    if not isinstance(stdout, str):
        return None
    bounded = stdout.encode("utf-8", errors="ignore")[-MAX_EXECUTOR_STDOUT_BYTES:]
    text = bounded.decode("utf-8", errors="ignore")
    candidates = [text, *reversed([line.strip() for line in text.splitlines()])]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            try:
                return sanitize_public_receipt(decoded)
            except ValueError:
                continue
    return None


def sanitize_public_receipt(receipt: Mapping[str, Any]) -> dict[str, object]:
    sanitized: dict[str, object] = {}
    for field in RECEIPT_FIELDS:
        value = receipt.get(field, _default_receipt_value(field))
        if isinstance(value, bool):
            sanitized[field] = value
        elif isinstance(value, int) and not isinstance(value, bool):
            if value < 0:
                raise ValueError("receipt_field_not_public_safe")
            sanitized[field] = value
        elif isinstance(value, str) and PUBLIC_VALUE_RE.fullmatch(value):
            sanitized[field] = value
        else:
            raise ValueError("receipt_field_not_public_safe")
    if sanitized["maintenance_task_id"] != TASK_ID:
        raise ValueError("receipt_task_id_mismatch")
    return sanitized


def receipt_status_lines(receipt: Mapping[str, object]) -> list[str]:
    return [f"{field}={receipt[field]}" for field in RECEIPT_FIELDS]


def success_criteria_met(receipt: Mapping[str, object]) -> bool:
    return (
        receipt.get("success_criteria") == "met"
        and receipt.get("request_accepted") is True
        and receipt.get("physically_verified") is True
        and receipt.get("display_power_status") == "off"
    )


def _metadata_lines(body: str) -> list[str]:
    metadata = (body or "").split("```task", 1)[0]
    return [line for line in metadata.splitlines() if not line.lstrip().startswith("#")]


def _public_idempotency_status(value: object) -> str:
    if isinstance(value, str) and PUBLIC_VALUE_RE.fullmatch(value):
        return value
    return "unavailable"


def _default_receipt_value(field: str) -> object:
    if field == "maintenance_task_id":
        return TASK_ID
    if field in {"request_accepted", "applied", "physically_verified"}:
        return False
    if field == "success_criteria":
        return "not_met"
    if field == "audit_receipt_hash":
        return "pending"
    return "unavailable"


def _blocked_receipt(reason: str) -> dict[str, object]:
    receipt: dict[str, object] = {
        "maintenance_task_id": TASK_ID,
        "request_accepted": False,
        "applied": False,
        "physically_verified": False,
        "display_power_status": "unverified",
        "idempotency_status": "not_executed",
        "executor_receipt_hash": "unavailable",
        "audit_receipt_hash": "pending",
        "stable_reason": reason,
        "success_criteria": "not_met",
    }
    receipt["audit_receipt_hash"] = _audit_hash(receipt)
    return receipt


def _audit_hash(receipt: Mapping[str, object]) -> str:
    payload = {
        key: value
        for key, value in receipt.items()
        if key in RECEIPT_FIELDS and key != "audit_receipt_hash"
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


DISPLAY_POWER_OFF_SCRIPT = r'''set -uo pipefail
TASK_ID="home_edge_01_display_power_off_v1"
request_accepted=false
applied=false
physically_verified=false
display_power_status="unverified"
stable_reason="not_started"
success_criteria="not_met"

emit_receipt() {
  printf '{"maintenance_task_id":"%s",' "$TASK_ID"
  printf '"request_accepted":%s,' "$request_accepted"
  printf '"applied":%s,' "$applied"
  printf '"physically_verified":%s,' "$physically_verified"
  printf '"display_power_status":"%s",' "$display_power_status"
  printf '"idempotency_status":"pending",'
  printf '"executor_receipt_hash":"pending",'
  printf '"audit_receipt_hash":"pending",'
  printf '"stable_reason":"%s",' "$stable_reason"
  printf '"success_criteria":"%s"}\n' "$success_criteria"
}

block() {
  stable_reason="$1"
  emit_receipt
  exit 10
}

monitor_state() {
  xset q 2>/dev/null | awk -F': ' '/Monitor is/ {print tolower($2); exit}'
}

request_accepted=true
if ! command -v xset >/dev/null 2>&1; then
  display_power_status="unavailable"
  block "xset_missing"
fi

before="$(monitor_state)"
if [ "$before" = "off" ]; then
  display_power_status="off"
  physically_verified=true
  stable_reason="already_off"
  success_criteria="met"
  emit_receipt
  exit 0
fi

if ! xset dpms force off >/dev/null 2>&1; then
  display_power_status="${before:-unverified}"
  block "display_power_off_command_failed"
fi
applied=true
sleep 1
after="$(monitor_state)"
if [ "$after" = "off" ]; then
  display_power_status="off"
  physically_verified=true
  stable_reason="completed"
  success_criteria="met"
  emit_receipt
  exit 0
fi

display_power_status="${after:-unverified}"
block "physical_verification_failed"
'''
