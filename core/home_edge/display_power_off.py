from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Protocol
from uuid import uuid4

from core.home_edge.controller_auth import resolve_exec_hmac_secret
from core.home_edge.executor import HomeEdgeExecError, HomeEdgeExecRequest, sign_request
from core.home_edge.executor_gateway import execute_home_edge_request

TASK_ID = "home_edge_01_display_power_off_v1"
REPOSITORY = "alanua/Skeleton"
TARGET_NODE = "home-edge-01"
OPERATOR_APPROVAL = "EXPLICIT_2026_08_09_TURN_OFF_HOME_EDGE_MONITOR"
RUN_AS = "desktop-user"
EXECUTION_LANE = "routine_mutation"
REQUEST_TIMEOUT_SECONDS = 20
MAX_EXECUTOR_OUTPUT_BYTES = 16_384
IDEMPOTENCY_KEY = "home-edge-display-off-controller-boundary-20260809-v1"
SIGNER_PATH = Path("/usr/local/sbin/home_edge_display_power_off_signer")
SIGNER_COMMAND = ["/usr/bin/sudo", "--non-interactive", "--", str(SIGNER_PATH)]
SIGNER_ENVIRONMENT = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"}
MAX_SIGNER_STDIN_BYTES = 512
EXPECTED_MAIN_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PUBLIC_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:=-]+$")
SIGNER_STDIN = {
    "schema": "skeleton.home_edge.display_power_off_signer_request.v1",
    "maintenance_task_id": TASK_ID,
    "operator_approval_ref": OPERATOR_APPROVAL,
    "target_node": TARGET_NODE,
}
SIGNER_STDIN_KEYS = frozenset(SIGNER_STDIN)
RECEIPT_FIELDS = (
    "maintenance_task_id",
    "request_accepted",
    "applied",
    "physically_verified",
    "display_power_state",
    "executor_receipt_hash",
    "stable_reason",
    "success_criteria",
)

DISPLAY_POWER_OFF_SCRIPT = r'''#!/usr/bin/env bash
set -euo pipefail
TASK_ID="home_edge_01_display_power_off_v1"
state="unknown"; accepted=false; applied=false; verified=false; reason="blocked"
json_bool() { if [ "$1" = true ]; then printf 'true'; else printf 'false'; fi; }
emit() {
  printf '{"public":{'
  printf '"maintenance_task_id":"%s",' "$TASK_ID"
  printf '"request_accepted":%s,' "$(json_bool "$accepted")"
  printf '"applied":%s,' "$(json_bool "$applied")"
  printf '"physically_verified":%s,' "$(json_bool "$verified")"
  printf '"display_power_state":"%s",' "$state"
  printf '"executor_receipt_hash":"pending",'
  printf '"stable_reason":"%s",' "$reason"
  printf '"success_criteria":"%s"' "$([ "$verified" = true ] && printf met || printf not_met)"
  printf '}}\n'
}
if ! command -v xset >/dev/null 2>&1; then reason="xset_missing"; emit; exit 0; fi
accepted=true
if xset dpms force off >/dev/null 2>&1; then applied=true; reason="display_off_requested"; else reason="display_off_command_failed"; emit; exit 0; fi
sleep 1
if xset q 2>/dev/null | grep -Eq 'Monitor is[[:space:]]+Off'; then state="off"; verified=true; reason="completed"; else state="not_off"; reason="display_off_not_verified"; fi
emit
'''

class FixedDisplayOffSigner(Protocol):
    def __call__(self, metadata: Mapping[str, str]) -> Mapping[str, Any]: ...


def execute_display_power_off_task(
    *,
    expected_main_sha: str,
    registered_clean_main_sha: str,
    github_main_sha: str,
    signer: FixedDisplayOffSigner | None = None,
) -> dict[str, object]:
    validate_main_sha(
        expected_main_sha,
        registered_clean_main_sha=registered_clean_main_sha,
        github_main_sha=github_main_sha,
    )
    signed_request = (signer or invoke_fixed_display_off_signer)(SIGNER_STDIN)
    validate_signed_display_off_request(signed_request)
    try:
        executor_receipt = execute_home_edge_request(signed_request)
    except (subprocess.TimeoutExpired, TimeoutError):
        return _blocked_receipt("executor_transport_timeout")
    except HomeEdgeExecError:
        return _blocked_receipt("executor_transport_failed")
    except Exception:
        return _blocked_receipt("executor_transport_exception")
    receipt = public_receipt_from_executor_stdout(executor_receipt.to_mapping())
    receipt["executor_receipt_hash"] = executor_receipt.receipt_hash
    if success_criteria_met(receipt):
        receipt["stable_reason"] = "completed"
        receipt["success_criteria"] = "met"
    return receipt


def validate_main_sha(
    expected_main_sha: str,
    *,
    registered_clean_main_sha: str,
    github_main_sha: str,
) -> None:
    for value in (expected_main_sha, registered_clean_main_sha, github_main_sha):
        if EXPECTED_MAIN_SHA_RE.fullmatch(value or "") is None:
            raise ValueError("main_sha_unavailable")
    if expected_main_sha != registered_clean_main_sha:
        raise ValueError("registered_clean_main_sha_mismatch")
    if expected_main_sha != github_main_sha:
        raise ValueError("github_main_sha_mismatch")


def invoke_fixed_display_off_signer(metadata: Mapping[str, str]) -> Mapping[str, Any]:
    completed = subprocess.run(
        SIGNER_COMMAND,
        input=json.dumps(dict(metadata), sort_keys=True, separators=(",", ":")) + "\n",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        check=False,
        env=dict(SIGNER_ENVIRONMENT),
    )
    if completed.returncode != 0:
        raise ValueError("display_power_off_signer_failed")
    try:
        decoded = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("display_power_off_signer_invalid_json") from exc
    if not isinstance(decoded, Mapping):
        raise ValueError("display_power_off_signer_invalid_envelope")
    return decoded


def signer_envelope_from_stdin(
    stdin_text: str,
    *,
    argv: list[str] | None = None,
) -> dict[str, Any]:
    if argv:
        raise ValueError("signer_argv_rejected")
    if len(stdin_text.encode("utf-8")) > MAX_SIGNER_STDIN_BYTES:
        raise ValueError("signer_stdin_too_large")
    try:
        metadata = json.loads(stdin_text)
    except json.JSONDecodeError as exc:
        raise ValueError("signer_stdin_invalid_json") from exc
    if not isinstance(metadata, Mapping) or set(metadata) != SIGNER_STDIN_KEYS:
        raise ValueError("signer_stdin_unknown_field")
    if dict(metadata) != SIGNER_STDIN:
        raise ValueError("signer_stdin_metadata_mismatch")
    return build_signed_display_off_request().to_mapping()


def build_signed_display_off_request() -> HomeEdgeExecRequest:
    secret = resolve_exec_hmac_secret()
    request = HomeEdgeExecRequest.from_mapping(
        {
            "request_id": f"{TASK_ID}-{uuid4()}",
            "node_id": TARGET_NODE,
            "execution_lane": EXECUTION_LANE,
            "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "idempotency_key": IDEMPOTENCY_KEY,
            "operator_approval_ref": OPERATOR_APPROVAL,
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


def validate_signed_display_off_request(
    request: Mapping[str, Any],
) -> HomeEdgeExecRequest:
    parsed = HomeEdgeExecRequest.from_mapping(request)
    checks = (
        parsed.node_id == TARGET_NODE,
        parsed.execution_lane.value == EXECUTION_LANE,
        parsed.run_as.value == RUN_AS,
        parsed.mode.value == "script",
        parsed.script == DISPLAY_POWER_OFF_SCRIPT,
        parsed.script_interpreter == "bash",
        parsed.timeout_seconds == REQUEST_TIMEOUT_SECONDS,
        parsed.max_output_bytes == MAX_EXECUTOR_OUTPUT_BYTES,
        parsed.idempotency_key == IDEMPOTENCY_KEY,
        parsed.operator_approval_ref == OPERATOR_APPROVAL,
        parsed.public is False,
        parsed.environment == {},
        parsed.cwd is None,
        parsed.stdin_text is None,
        parsed.stdin_base64 is None,
        parsed.argv == (),
        isinstance(parsed.signature, str) and parsed.signature.startswith("sha256="),
        parsed.request_id.startswith(f"{TASK_ID}-"),
        bool(parsed.nonce) and parsed.nonce.startswith(f"{TASK_ID}-"),
    )
    if not all(checks):
        raise ValueError("display_power_off_signed_request_authority_mismatch")
    return parsed


def public_receipt_from_executor_stdout(
    receipt: Mapping[str, Any],
) -> dict[str, object]:
    if receipt.get("status") != "ok" or receipt.get("exit_code") != 0:
        return _blocked_receipt("executor_receipt_not_ok")
    stdout = receipt.get("stdout")
    if not isinstance(stdout, str):
        return _blocked_receipt("executor_stdout_not_json")
    try:
        decoded = json.loads(stdout)
    except json.JSONDecodeError:
        return _blocked_receipt("executor_stdout_not_json")
    public = decoded.get("public")
    if not isinstance(public, Mapping):
        return _blocked_receipt("executor_public_receipt_missing")
    try:
        return sanitize_public_receipt(public)
    except ValueError:
        return _blocked_receipt("executor_public_receipt_unsafe")


def sanitize_public_receipt(receipt: Mapping[str, Any]) -> dict[str, object]:
    sanitized: dict[str, object] = {}
    for field in RECEIPT_FIELDS:
        if field == "executor_receipt_hash":
            continue
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
    if sanitized["display_power_state"] not in {"off", "not_off", "unknown"}:
        raise ValueError("display_power_state_invalid")
    return sanitized


def success_criteria_met(receipt: Mapping[str, object]) -> bool:
    return (
        receipt.get("success_criteria") == "met"
        and receipt.get("request_accepted") is True
        and receipt.get("applied") is True
        and receipt.get("physically_verified") is True
        and receipt.get("display_power_state") == "off"
    )


def _blocked_receipt(
    reason: str,
    *,
    executor_receipt_hash: str = "none",
) -> dict[str, object]:
    return {
        "maintenance_task_id": TASK_ID,
        "request_accepted": False,
        "applied": False,
        "physically_verified": False,
        "display_power_state": "unknown",
        "executor_receipt_hash": executor_receipt_hash,
        "stable_reason": reason,
        "success_criteria": "not_met",
    }
