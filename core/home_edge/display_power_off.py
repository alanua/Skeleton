from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from typing import Any, Mapping
from uuid import uuid4

from core.home_edge.controller_auth import read_fixed_controller_hmac_secret
from core.home_edge.executor import HomeEdgeExecError, HomeEdgeExecRequest, sign_request
from core.home_edge.executor_gateway import execute_home_edge_request


TASK_ID = "home_edge_01_display_power_off_v1"
MODE = "RUNTIME_MAINTENANCE_TASK"
RISK = "yellow"
TARGET_NODE = "home-edge-01"
OPERATOR_APPROVAL = "EXPLICIT_2026_08_09_TURN_OFF_HOME_EDGE_MONITOR"
PRIVACY_BOUNDARY = "PRIVATE_RUNTIME_STATE_PUBLIC_SAFE_STATUS"
RUN_AS = "desktop-user"
EXECUTION_LANE = "routine_mutation"
REQUEST_TIMEOUT_SECONDS = 20
MAX_EXECUTOR_OUTPUT_BYTES = 64_000
IDEMPOTENCY_KEY_PREFIX = "home-edge-01-display-power-off-v1"
RECEIPT_SCHEMA = "skeleton.home_edge.display_power_off_receipt.v1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
FIELD_RE = re.compile(r"^\s*(?P<field>[A-Za-z][A-Za-z0-9 _-]{0,80}):\s*(?P<value>.*?)\s*$")
PUBLIC_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:=-]+$")
AUTH_CONFIG_REASON_RE = re.compile(
    r"^executor_auth_config_(?:missing|unsafe|invalid)$"
)
ALLOWED_FIELDS = frozenset(
    {
        "Mode",
        "Maintenance Task ID",
        "Risk",
        "Target Node",
        "Operator Approval",
        "Privacy Boundary",
    }
)
RECEIPT_FIELDS = (
    "maintenance_task_id",
    "request_accepted",
    "applied",
    "physically_verified",
    "physical_observation",
    "physical_verification_reason",
    "executor_receipt_hash",
    "stable_reason",
    "success_criteria",
)


def execute_display_power_off_task(
    body: str,
    *,
    registered_clean_main_sha: str,
    github_main_sha: str,
) -> dict[str, object]:
    parse_runtime_input(body)
    validate_runtime_main_sha(
        registered_clean_main_sha=registered_clean_main_sha,
        github_main_sha=github_main_sha,
    )
    try:
        request = build_display_power_off_request()
    except ValueError as exc:
        reason = exc.args[0] if exc.args else ""
        if isinstance(reason, str) and AUTH_CONFIG_REASON_RE.fullmatch(reason):
            return _blocked_receipt(reason)
        raise
    try:
        executor_receipt = execute_home_edge_request(request.to_mapping())
    except (subprocess.TimeoutExpired, TimeoutError):
        return _blocked_receipt("executor_transport_timeout", request_accepted=False)
    except HomeEdgeExecError:
        return _blocked_receipt("executor_transport_failed", request_accepted=False)
    except Exception:
        return _blocked_receipt("executor_transport_exception", request_accepted=False)

    receipt = public_receipt_from_executor_stdout(executor_receipt.to_mapping())
    receipt["executor_receipt_hash"] = executor_receipt.receipt_hash
    return receipt


def parse_runtime_input(body: str) -> dict[str, str]:
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
    if sorted(set(fields) - ALLOWED_FIELDS):
        raise ValueError("unknown_runtime_input_field")
    expected = {
        "Mode": MODE,
        "Maintenance Task ID": TASK_ID,
        "Risk": RISK,
        "Target Node": TARGET_NODE,
        "Operator Approval": OPERATOR_APPROVAL,
        "Privacy Boundary": PRIVACY_BOUNDARY,
    }
    for field, value in expected.items():
        if fields.get(field) != value:
            raise ValueError(f"{_reason_token(field)}_mismatch")
    return fields


def validate_runtime_main_sha(
    *,
    registered_clean_main_sha: str,
    github_main_sha: str,
) -> None:
    if SHA_RE.fullmatch(registered_clean_main_sha or "") is None:
        raise ValueError("registered_clean_main_sha_unavailable")
    if SHA_RE.fullmatch(github_main_sha or "") is None:
        raise ValueError("github_main_sha_unavailable")
    if registered_clean_main_sha != github_main_sha:
        raise ValueError("trusted_runtime_main_sha_mismatch")


def build_display_power_off_request() -> HomeEdgeExecRequest:
    secret = read_fixed_controller_hmac_secret()
    request = HomeEdgeExecRequest.from_mapping(
        {
            "request_id": f"{TASK_ID}-{uuid4()}",
            "node_id": TARGET_NODE,
            "execution_lane": EXECUTION_LANE,
            "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "operator_approval_ref": OPERATOR_APPROVAL,
            "idempotency_key": f"{IDEMPOTENCY_KEY_PREFIX}-{uuid4()}",
            "run_as": RUN_AS,
            "mode": "script",
            "script": DISPLAY_POWER_OFF_SCRIPT,
            "script_interpreter": "bash",
            "timestamp": datetime.now(UTC).isoformat(),
            "nonce": f"{TASK_ID}-{uuid4()}",
            "max_output_bytes": MAX_EXECUTOR_OUTPUT_BYTES,
            "public": False,
        }
    )
    return HomeEdgeExecRequest.from_mapping(
        {**request.to_mapping(include_signature=False), "signature": sign_request(request, secret)}
    )


def public_receipt_from_executor_stdout(receipt: Mapping[str, Any]) -> dict[str, object]:
    if receipt.get("status") != "ok":
        return _blocked_receipt("executor_receipt_not_ok", request_accepted=False)
    decoded = _decode_stdout(receipt.get("stdout"))
    if decoded is None:
        return _blocked_receipt("executor_stdout_not_json", request_accepted=True)
    public = decoded.get("public")
    if not isinstance(public, Mapping):
        return _blocked_receipt("executor_public_receipt_missing", request_accepted=True)
    try:
        return sanitize_public_receipt(public)
    except ValueError:
        return _blocked_receipt("executor_public_receipt_unsafe", request_accepted=True)


def sanitize_public_receipt(receipt: Mapping[str, Any]) -> dict[str, object]:
    sanitized: dict[str, object] = {}
    for field in RECEIPT_FIELDS:
        if field not in receipt:
            raise ValueError("receipt_field_missing")
        value = receipt[field]
        if isinstance(value, bool):
            sanitized[field] = value
        elif isinstance(value, str) and PUBLIC_VALUE_RE.fullmatch(value):
            sanitized[field] = value
        else:
            raise ValueError("receipt_field_not_public_safe")
    if sanitized["maintenance_task_id"] != TASK_ID:
        raise ValueError("receipt_task_id_mismatch")
    if sanitized["physical_observation"] not in {"off", "on", "unknown"}:
        raise ValueError("physical_observation_invalid")
    if sanitized["success_criteria"] not in {"met", "not_met", "pending"}:
        raise ValueError("success_criteria_invalid")
    return sanitized


def receipt_status_lines(receipt: Mapping[str, object]) -> list[str]:
    return [f"{field}={receipt[field]}" for field in RECEIPT_FIELDS]


def success_criteria_met(receipt: Mapping[str, object]) -> bool:
    return (
        receipt.get("success_criteria") == "met"
        and receipt.get("request_accepted") is True
        and receipt.get("applied") is True
        and receipt.get("physically_verified") is True
    )


def _blocked_receipt(
    reason: str,
    *,
    request_accepted: bool = False,
    applied: bool = False,
    physically_verified: bool = False,
    physical_observation: str = "unknown",
    executor_receipt_hash: str = "not_available",
) -> dict[str, object]:
    return {
        "maintenance_task_id": TASK_ID,
        "request_accepted": request_accepted,
        "applied": applied,
        "physically_verified": physically_verified,
        "physical_observation": physical_observation,
        "physical_verification_reason": reason,
        "executor_receipt_hash": executor_receipt_hash,
        "stable_reason": reason,
        "success_criteria": "not_met",
    }


def _decode_stdout(value: Any) -> Mapping[str, Any] | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        decoded = json.loads(text.splitlines()[-1])
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, Mapping) else None


def _metadata_lines(body: str) -> list[str]:
    lines: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            if lines:
                break
            continue
        if FIELD_RE.match(line) is None and lines:
            break
        lines.append(raw_line)
    return lines


def _reason_token(field: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", field.lower()).strip("_")


DISPLAY_POWER_OFF_SCRIPT = r"""#!/usr/bin/env bash
set -u

TASK_ID="home_edge_01_display_power_off_v1"
request_accepted=true
applied=false
physically_verified=false
physical_observation="unknown"
physical_verification_reason="physical_state_unobservable"
stable_reason="physical_state_unobservable"

json_bool() {
  if [ "$1" = "true" ]; then printf true; else printf false; fi
}

emit_receipt() {
  local success_criteria="not_met"
  if [ "$request_accepted" = "true" ] && [ "$applied" = "true" ] && [ "$physically_verified" = "true" ]; then
    success_criteria="met"
    stable_reason="completed"
  fi
  printf '{"public":{'
  printf '"maintenance_task_id":"%s",' "$TASK_ID"
  printf '"request_accepted":%s,' "$(json_bool "$request_accepted")"
  printf '"applied":%s,' "$(json_bool "$applied")"
  printf '"physically_verified":%s,' "$(json_bool "$physically_verified")"
  printf '"physical_observation":"%s",' "$physical_observation"
  printf '"physical_verification_reason":"%s",' "$physical_verification_reason"
  printf '"executor_receipt_hash":"pending",'
  printf '"stable_reason":"%s",' "$stable_reason"
  printf '"success_criteria":"%s"' "$success_criteria"
  printf '}}\n'
}

if command -v xset >/dev/null 2>&1; then
  if xset dpms force off >/dev/null 2>&1; then
    applied=true
  else
    stable_reason="display_power_off_command_failed"
    physical_verification_reason="display_power_off_command_failed"
  fi
  sleep 1
  xset_output="$(xset q 2>/dev/null || true)"
  if printf '%s\n' "$xset_output" | grep -Eq 'Monitor is Off|Monitor is in Off'; then
    physically_verified=true
    physical_observation="off"
    physical_verification_reason="dpms_monitor_off"
  elif [ -n "$xset_output" ]; then
    physical_observation="on"
    physical_verification_reason="dpms_monitor_not_off"
    stable_reason="physical_state_not_off"
  fi
else
  stable_reason="xset_unavailable"
  physical_verification_reason="physical_state_unobservable"
fi

emit_receipt
"""
