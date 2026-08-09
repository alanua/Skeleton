from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from core.home_edge.controller_auth import (
    AUTH_CONFIG_REASON_RE,
    read_controller_exec_hmac_secret,
)
from core.home_edge.executor import HomeEdgeExecError, HomeEdgeExecRequest, sign_request
from core.home_edge.executor_gateway import execute_home_edge_request


TASK_ID = "home_edge_01_display_power_off_v1"
REPOSITORY = "alanua/Skeleton"
TARGET_NODE = "home-edge-01"
EXECUTION_LANE = "routine_mutation"
RUN_AS = "desktop-user"
OPERATOR_APPROVAL_REF = "EXPLICIT_2026_08_09_TURN_OFF_HOME_EDGE_MONITOR"
REQUEST_TIMEOUT_SECONDS = 30
MAX_EXECUTOR_OUTPUT_BYTES = 64_000
IDEMPOTENCY_KEY = "home-edge-display-off-controller-boundary-20260809-v1"
SIGNER_STDIN_SCHEMA = "skeleton.home_edge.display_power_off.signer_input.v1"
RECEIPT_SCHEMA = "skeleton.home_edge.display_power_off_receipt.v1"
FIXED_SIGNER_PATH = Path("/usr/local/sbin/skeleton-home-edge-display-off-controller-signer")
SUDO_INVOKE = ("/usr/bin/sudo", "--non-interactive", "--", str(FIXED_SIGNER_PATH))
EXPECTED_MAIN_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
FIELD_RE = re.compile(r"^\s*(?P<field>[A-Za-z][A-Za-z0-9 _-]{0,80}):\s*(?P<value>.*?)\s*$")
PUBLIC_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:=-]+$")

ALLOWED_FIELDS = frozenset(
    {
        "Mode",
        "Maintenance Task ID",
        "Repository",
        "Expected Main SHA",
        "Target",
    }
)
RECEIPT_FIELDS = (
    "maintenance_task_id",
    "request_accepted",
    "applied",
    "physically_verified",
    "display_state",
    "executor_receipt_hash",
    "stable_reason",
    "success_criteria",
)


@dataclass(frozen=True)
class RuntimeInput:
    repository: str
    expected_main_sha: str
    target: str


def execute_display_power_off_task(
    body: str,
    *,
    registered_clean_main_sha: str,
    github_main_sha: str,
    signer_command: tuple[str, ...] = SUDO_INVOKE,
) -> dict[str, object]:
    runtime_input = parse_runtime_input(body)
    validate_main_sha(
        runtime_input.expected_main_sha,
        registered_clean_main_sha=registered_clean_main_sha,
        github_main_sha=github_main_sha,
    )
    try:
        signed = request_signed_display_power_off_request(signer_command=signer_command)
    except ValueError as exc:
        reason = exc.args[0] if exc.args else ""
        if isinstance(reason, str) and (
            AUTH_CONFIG_REASON_RE.fullmatch(reason) or reason.startswith("signer_")
        ):
            return _blocked_receipt(reason)
        raise
    try:
        request = validate_signed_display_power_off_request(signed)
    except ValueError as exc:
        return _blocked_receipt(str(exc) or "signed_request_invalid")
    try:
        executor_receipt = execute_home_edge_request(request)
    except (subprocess.TimeoutExpired, TimeoutError):
        return _blocked_receipt("executor_transport_timeout")
    except HomeEdgeExecError:
        return _blocked_receipt("executor_transport_failed")
    except Exception:
        return _blocked_receipt("executor_transport_exception")

    receipt = public_receipt_from_executor_stdout(executor_receipt.to_mapping())
    receipt["executor_receipt_hash"] = executor_receipt.receipt_hash
    return receipt


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
    if sorted(set(fields) - ALLOWED_FIELDS):
        raise ValueError("unknown_runtime_input_field")
    if fields.get("Mode") != "RUNTIME_MAINTENANCE_TASK":
        raise ValueError("runtime_mode_mismatch")
    if fields.get("Maintenance Task ID") != TASK_ID:
        raise ValueError("maintenance_task_id_mismatch")
    runtime_input = RuntimeInput(
        repository=fields.get("Repository", ""),
        expected_main_sha=fields.get("Expected Main SHA", ""),
        target=fields.get("Target", ""),
    )
    if runtime_input.repository != REPOSITORY:
        raise ValueError("repository_mismatch")
    if EXPECTED_MAIN_SHA_RE.fullmatch(runtime_input.expected_main_sha) is None:
        raise ValueError("expected_main_sha_malformed")
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


def request_signed_display_power_off_request(
    *,
    signer_command: tuple[str, ...] = SUDO_INVOKE,
) -> dict[str, Any]:
    if signer_command != SUDO_INVOKE:
        raise ValueError("signer_command_mismatch")
    payload = {
        "schema": SIGNER_STDIN_SCHEMA,
        "task_id": TASK_ID,
        "target_node": TARGET_NODE,
        "operator_approval_ref": OPERATOR_APPROVAL_REF,
        "idempotency_key": IDEMPOTENCY_KEY,
    }
    completed = subprocess.run(
        list(signer_command),
        input=json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        check=False,
        env=_safe_signer_child_environment(),
    )
    if completed.returncode != 0:
        raise ValueError("signer_failed")
    if completed.stderr.strip():
        raise ValueError("signer_stderr_not_empty")
    try:
        decoded = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("signer_stdout_invalid") from exc
    if not isinstance(decoded, dict):
        raise ValueError("signer_stdout_invalid")
    return decoded


def signer_main(argv: list[str] | None = None, stdin: str | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    try:
        output = build_signed_display_power_off_request_from_signer_input(
            argv=argv,
            stdin=sys.stdin.read() if stdin is None else stdin,
        )
        print(json.dumps(output, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:  # noqa: BLE001 - signer must fail closed without private details.
        reason = exc.args[0] if isinstance(exc, ValueError) and exc.args else "signer_failed"
        if not isinstance(reason, str) or not reason.startswith(("signer_", "executor_auth_config_")):
            reason = "signer_failed"
        print(json.dumps({"status": "blocked", "reason": reason}, sort_keys=True), file=sys.stderr)
        return 2


def build_signed_display_power_off_request_from_signer_input(
    *,
    argv: list[str],
    stdin: str,
) -> dict[str, Any]:
    validate_signer_invocation(argv=argv, stdin=stdin)
    secret = read_controller_exec_hmac_secret()
    request = build_unsigned_display_power_off_request()
    return {
        **request.to_mapping(include_signature=False),
        "signature": sign_request(request, secret),
    }


def validate_signer_invocation(*, argv: list[str], stdin: str) -> None:
    if argv:
        raise ValueError("signer_argv_rejected")
    try:
        decoded = json.loads(stdin)
    except json.JSONDecodeError as exc:
        raise ValueError("signer_stdin_invalid") from exc
    expected = {
        "schema": SIGNER_STDIN_SCHEMA,
        "task_id": TASK_ID,
        "target_node": TARGET_NODE,
        "operator_approval_ref": OPERATOR_APPROVAL_REF,
        "idempotency_key": IDEMPOTENCY_KEY,
    }
    if decoded != expected:
        raise ValueError("signer_stdin_rejected")


def build_unsigned_display_power_off_request() -> HomeEdgeExecRequest:
    return HomeEdgeExecRequest.from_mapping(
        {
            "request_id": f"{TASK_ID}-{uuid4()}",
            "node_id": TARGET_NODE,
            "execution_lane": EXECUTION_LANE,
            "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "operator_approval_ref": OPERATOR_APPROVAL_REF,
            "idempotency_key": IDEMPOTENCY_KEY,
            "run_as": RUN_AS,
            "mode": "script",
            "script": DISPLAY_OFF_SCRIPT,
            "script_interpreter": "python3",
            "timestamp": datetime.now(UTC).isoformat(),
            "nonce": f"{TASK_ID}-{uuid4()}",
            "max_output_bytes": MAX_EXECUTOR_OUTPUT_BYTES,
            "public": False,
        }
    )


def validate_signed_display_power_off_request(request: Mapping[str, Any]) -> dict[str, Any]:
    if request.get("node_id") != TARGET_NODE:
        raise ValueError("signed_request_node_rejected")
    if request.get("execution_lane") != EXECUTION_LANE:
        raise ValueError("signed_request_lane_rejected")
    if request.get("run_as") != RUN_AS:
        raise ValueError("signed_request_run_as_rejected")
    if request.get("mode") != "script":
        raise ValueError("signed_request_mode_rejected")
    if request.get("script") != DISPLAY_OFF_SCRIPT:
        raise ValueError("signed_request_script_rejected")
    if request.get("script_interpreter") != "python3":
        raise ValueError("signed_request_interpreter_rejected")
    if request.get("timeout_seconds") != REQUEST_TIMEOUT_SECONDS:
        raise ValueError("signed_request_timeout_rejected")
    if request.get("max_output_bytes") != MAX_EXECUTOR_OUTPUT_BYTES:
        raise ValueError("signed_request_output_rejected")
    if request.get("idempotency_key") != IDEMPOTENCY_KEY:
        raise ValueError("signed_request_idempotency_rejected")
    if request.get("operator_approval_ref") != OPERATOR_APPROVAL_REF:
        raise ValueError("signed_request_approval_rejected")
    if request.get("public") is not False:
        raise ValueError("signed_request_public_rejected")
    if request.get("environment") != {}:
        raise ValueError("signed_request_environment_rejected")
    if request.get("cwd") is not None:
        raise ValueError("signed_request_cwd_rejected")
    if "stdin_text" in request or "stdin_base64" in request:
        raise ValueError("signed_request_stdin_rejected")
    if request.get("argv") != []:
        raise ValueError("signed_request_argv_rejected")
    parsed = HomeEdgeExecRequest.from_mapping(request)
    mapping = parsed.to_mapping()
    signature = mapping.get("signature")
    if not isinstance(signature, str) or not re.fullmatch(r"sha256=[0-9a-f]{64}", signature):
        raise ValueError("signed_request_signature_rejected")
    return mapping


def public_receipt_from_executor_stdout(receipt: Mapping[str, Any]) -> dict[str, object]:
    if receipt.get("status") != "ok" or receipt.get("exit_code") != 0:
        return _blocked_receipt("executor_receipt_not_ok")
    decoded = _decode_stdout(receipt.get("stdout"))
    if decoded is None:
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
    if sanitized["display_state"] not in {"off", "on", "unknown"}:
        raise ValueError("receipt_display_state_invalid")
    return sanitized


def receipt_status_lines(receipt: Mapping[str, object]) -> list[str]:
    return [f"{field}={receipt[field]}" for field in RECEIPT_FIELDS]


def success_criteria_met(receipt: Mapping[str, object]) -> bool:
    return (
        receipt.get("success_criteria") == "met"
        and receipt.get("request_accepted") is True
        and receipt.get("applied") is True
        and receipt.get("physically_verified") is True
        and receipt.get("display_state") == "off"
    )


def _blocked_receipt(reason: str, *, executor_receipt_hash: str = "not_available") -> dict[str, object]:
    return {
        "maintenance_task_id": TASK_ID,
        "request_accepted": False,
        "applied": False,
        "physically_verified": False,
        "display_state": "unknown",
        "executor_receipt_hash": executor_receipt_hash,
        "stable_reason": reason if PUBLIC_VALUE_RE.fullmatch(reason) else "blocked",
        "success_criteria": "not_met",
    }


def _decode_stdout(stdout: Any) -> dict[str, Any] | None:
    if not isinstance(stdout, str):
        return None
    try:
        decoded = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _metadata_lines(body: str) -> list[str]:
    lines: list[str] = []
    for line in body.splitlines():
        if line.strip().startswith("```"):
            break
        lines.append(line)
    return lines


def _safe_signer_child_environment() -> dict[str, str]:
    env: dict[str, str] = {}
    for name in ("PATH", "LANG", "LC_ALL", "LC_CTYPE"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


DISPLAY_OFF_SCRIPT = r'''import json
import shutil
import subprocess

TASK_ID = "home_edge_01_display_power_off_v1"


def run(command):
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        check=False,
    )
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def observe_state():
    if shutil.which("xset"):
        code, out, _err = run(["xset", "-q"])
        if code == 0:
            lowered = out.lower()
            if "monitor is off" in lowered:
                return "off"
            if "monitor is on" in lowered:
                return "on"
    return "unknown"


def main():
    accepted = True
    applied = False
    physically_verified = False
    state = "unknown"
    reason = "not_verified"

    if shutil.which("xset"):
        code, _out, _err = run(["xset", "dpms", "force", "off"])
        applied = code == 0
        state = observe_state()
        physically_verified = state == "off"
        if applied and physically_verified:
            reason = "completed"
        elif applied:
            reason = "unobservable"
    elif shutil.which("wlr-randr"):
        code, out, _err = run(["wlr-randr"])
        names = [
            line.split()[0]
            for line in out.splitlines()
            if line and not line.startswith(" ") and not line.startswith("\t")
        ] if code == 0 else []
        applied = bool(names)
        for name in names:
            off_code, _off_out, _off_err = run(["wlr-randr", "--output", name, "--off"])
            applied = applied and off_code == 0
        state = "unknown"
        reason = "unobservable" if applied else "apply_failed"
    else:
        reason = "display_control_unavailable"

    public = {
        "maintenance_task_id": TASK_ID,
        "request_accepted": accepted,
        "applied": applied,
        "physically_verified": physically_verified,
        "display_state": state,
        "executor_receipt_hash": "pending",
        "stable_reason": reason,
        "success_criteria": "met" if accepted and applied and physically_verified and state == "off" else "not_met",
    }
    print(json.dumps({"public": public}, sort_keys=True, separators=(",", ":")))


main()
'''
