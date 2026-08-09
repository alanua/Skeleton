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
OPERATION_ID = TASK_ID
IDEMPOTENCY_KEY = "home-edge-01-display-power-off-20260809-v1"
REQUEST_TIMEOUT_SECONDS = 90
RECEIPT_SCHEMA = "skeleton.home_edge.display_power_off_receipt.v1"
EXPECTED_MAIN_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_MAX_EXECUTOR_STDOUT_BYTES = 65536

RECEIPT_FIELDS = (
    "maintenance_task_id",
    "operation_id",
    "request_accepted",
    "applied",
    "physically_verified",
    "mutation_executor_receipt_hash",
    "audit_receipt_ref",
    "audit_receipt_hash",
    "stable_reason",
    "success_criteria",
)

_ALLOWED_FIELDS = frozenset(
    {
        "Mode",
        "Maintenance Task ID",
        "Repository",
        "Expected Main SHA",
        "Operator Approval",
        "Target Node",
    }
)
_FIELD_RE = re.compile(r"^\s*(?P<field>[A-Za-z][A-Za-z0-9 _-]{0,80}):\s*(?P<value>.*?)\s*$")
_PUBLIC_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:=-]+$")


@dataclass(frozen=True)
class RuntimeInput:
    repository: str
    expected_main_sha: str
    operator_approval: str
    target_node: str


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
    public["mutation_executor_receipt_hash"] = executor_receipt.receipt_hash
    public["audit_receipt_hash"] = _audit_hash(public)
    if not success_criteria_met(public):
        public["success_criteria"] = "not_met"
        public["audit_receipt_hash"] = _audit_hash(public)
    return public


def parse_runtime_input(body: str) -> RuntimeInput:
    fields: dict[str, str] = {}
    duplicates: set[str] = set()
    for line in _metadata_lines(body):
        match = _FIELD_RE.match(line)
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
    unknown = sorted(set(fields) - _ALLOWED_FIELDS)
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
        target_node=fields.get("Target Node", ""),
    )
    if runtime_input.repository != REPOSITORY:
        raise ValueError("repository_mismatch")
    if EXPECTED_MAIN_SHA_RE.fullmatch(runtime_input.expected_main_sha) is None:
        raise ValueError("expected_main_sha_malformed")
    if not runtime_input.operator_approval:
        raise ValueError("missing_operator_approval")
    if runtime_input.operator_approval != OPERATOR_APPROVAL:
        raise ValueError("operator_approval_mismatch")
    if runtime_input.target_node != TARGET_NODE:
        raise ValueError("target_node_mismatch")
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
            "max_output_bytes": _MAX_EXECUTOR_STDOUT_BYTES,
        }
    )
    return HomeEdgeExecRequest.from_mapping(
        {
            **request.to_mapping(include_signature=False),
            "signature": sign_request(request, secret),
        }
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
    if not isinstance(receipt.get("stdout"), str):
        return _blocked_receipt("executor_stdout_missing")
    return _blocked_receipt("executor_stdout_not_json")


def _public_receipt_from_stdout(stdout: Any) -> dict[str, object] | None:
    if not isinstance(stdout, str):
        return None
    bounded = stdout.encode("utf-8", errors="ignore")[-_MAX_EXECUTOR_STDOUT_BYTES:]
    text = bounded.decode("utf-8", errors="ignore")
    candidates = [text, *reversed([line.strip() for line in text.splitlines()])]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(decoded, dict):
            continue
        try:
            return sanitize_public_receipt(decoded)
        except ValueError:
            continue
    return None


def sanitize_public_receipt(receipt: Mapping[str, Any]) -> dict[str, object]:
    sanitized: dict[str, object] = {}
    for field in RECEIPT_FIELDS:
        if field not in receipt:
            raise ValueError("receipt_field_missing")
        value = receipt[field]
        if isinstance(value, bool):
            sanitized[field] = value
        elif isinstance(value, str) and _PUBLIC_VALUE_RE.fullmatch(value):
            sanitized[field] = value
        else:
            raise ValueError("receipt_field_not_public_safe")
    if sanitized["maintenance_task_id"] != TASK_ID:
        raise ValueError("receipt_task_id_mismatch")
    if sanitized["operation_id"] != OPERATION_ID:
        raise ValueError("receipt_operation_id_mismatch")
    if sanitized["physically_verified"] not in {"yes", "no", "unobservable"}:
        raise ValueError("receipt_physically_verified_invalid")
    return sanitized


def receipt_status_lines(receipt: Mapping[str, object]) -> list[str]:
    return [f"{field}={receipt[field]}" for field in RECEIPT_FIELDS]


def success_criteria_met(receipt: Mapping[str, object]) -> bool:
    return (
        receipt.get("success_criteria") == "met"
        and receipt.get("request_accepted") is True
        and receipt.get("applied") is True
        and receipt.get("physically_verified") in {"yes", "unobservable"}
    )


def _metadata_lines(body: str) -> list[str]:
    metadata = (body or "").split("```task", 1)[0]
    return [line for line in metadata.splitlines() if not line.lstrip().startswith("#")]


def _blocked_receipt(reason: str) -> dict[str, object]:
    receipt: dict[str, object] = {
        "maintenance_task_id": TASK_ID,
        "operation_id": OPERATION_ID,
        "request_accepted": False,
        "applied": False,
        "physically_verified": "no",
        "mutation_executor_receipt_hash": "unavailable",
        "audit_receipt_ref": "unavailable",
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
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


DISPLAY_POWER_OFF_SCRIPT = r'''#!/usr/bin/env bash
set -u

TASK_ID="home_edge_01_display_power_off_v1"
OPERATION_ID="home_edge_01_display_power_off_v1"
REGISTRY_CLI="/home/oleksii/.local/bin/skeleton-devices"
STATE_ROOT="/var/lib/skeleton/home-edge-01/display-power-off-v1"
DISPLAY_VALUE=":0"
XAUTHORITY_VALUE="/home/oleksii/.Xauthority"

request_accepted=false
applied=false
physically_verified="no"
audit_receipt_ref="home_edge_exec_audit"
stable_reason="blocked"

emit_receipt() {
  criteria="$1"
  hash="$(printf '%s:%s:%s:%s:%s:%s' "$TASK_ID" "$OPERATION_ID" "$request_accepted" "$applied" "$physically_verified" "$stable_reason" | sha256sum | awk '{print $1}')"
  printf '{"maintenance_task_id":"%s",' "$TASK_ID"
  printf '"operation_id":"%s",' "$OPERATION_ID"
  printf '"request_accepted":%s,' "$request_accepted"
  printf '"applied":%s,' "$applied"
  printf '"physically_verified":"%s",' "$physically_verified"
  printf '"mutation_executor_receipt_hash":"pending",'
  printf '"audit_receipt_ref":"%s",' "$audit_receipt_ref"
  printf '"audit_receipt_hash":"%s",' "$hash"
  printf '"stable_reason":"%s",' "$stable_reason"
  printf '"success_criteria":"%s"}\n' "$criteria"
}

block() { stable_reason="$1"; emit_receipt not_met; exit 10; }

[ -r /etc/os-release ] || block os_identity_unavailable
. /etc/os-release
[ "${ID:-}" = "debian" ] || block os_not_debian
[ "$(hostname)" = "home-edge-01" ] || block node_identity_mismatch
[ -S /run/user/1000/bus ] || block desktop_session_bus_unavailable
command -v xset >/dev/null 2>&1 || block display_power_tool_unavailable

install -d -m 0700 "$STATE_ROOT" "$STATE_ROOT/logs" || block state_root_unavailable
if [ -x "$REGISTRY_CLI" ]; then
  "$REGISTRY_CLI" doctor >"$STATE_ROOT/logs/registry-doctor.log" 2>&1 || block canonical_registry_doctor_failed
  "$REGISTRY_CLI" list >"$STATE_ROOT/logs/registry-list.log" 2>&1 || block canonical_registry_list_failed
  "$REGISTRY_CLI" changes >"$STATE_ROOT/logs/registry-changes.log" 2>&1 || block canonical_registry_changes_failed
  "$REGISTRY_CLI" operations >"$STATE_ROOT/logs/registry-operations.log" 2>&1 || block canonical_registry_operations_failed
fi

request_accepted=true
if DISPLAY="$DISPLAY_VALUE" XAUTHORITY="$XAUTHORITY_VALUE" xset dpms force off >"$STATE_ROOT/logs/display-off.log" 2>&1; then
  applied=true
else
  stable_reason="display_power_off_failed"
  emit_receipt not_met
  exit 20
fi

if DISPLAY="$DISPLAY_VALUE" XAUTHORITY="$XAUTHORITY_VALUE" xset -q >"$STATE_ROOT/logs/display-verify.log" 2>&1; then
  if grep -qi 'Monitor is Off' "$STATE_ROOT/logs/display-verify.log"; then
    physically_verified="yes"
    stable_reason="completed"
    emit_receipt met
    exit 0
  fi
  physically_verified="unobservable"
  stable_reason="applied_not_physically_observable"
  emit_receipt met
  exit 0
fi

physically_verified="unobservable"
stable_reason="applied_not_physically_observable"
emit_receipt met
exit 0
'''
