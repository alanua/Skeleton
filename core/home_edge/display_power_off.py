from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from .controller_auth import (
    AUTHORITY_OPERATOR_APPROVAL,
    DISPLAY_POWER_OFF_TASK_ID,
    DisplayOffAuthority,
    validate_display_off_authority,
)
from .executor import (
    DEFAULT_NODE_ID,
    EXEC_REQUEST_SCHEMA,
    ExecMode,
    ExecutionLane,
    ExecutionUser,
    HomeEdgeExecError,
    HomeEdgeExecReceipt,
    HomeEdgeExecRequest,
)
from .executor_gateway import execute_home_edge_request


SIGNED_ENVELOPE_SCHEMA = "skeleton.home_edge.display_power_off.signed_envelope.v1"
FIXED_SIGNER_PATH = Path("/usr/local/libexec/skeleton/home-edge-display-off-controller-signer/current/home_edge_display_power_off_signer.py")
MAX_SIGNER_STDIN_BYTES = 16_384
SIGNER_TIMEOUT_SECONDS = 10
DISPLAY_OFF_REQUEST_ID = "home-edge-01-display-power-off-v1"
DISPLAY_OFF_IDEMPOTENCY_KEY = "home-edge-01-display-power-off-v1"
DISPLAY_OFF_TIMEOUT_SECONDS = 30
DISPLAY_OFF_MAX_OUTPUT_BYTES = 16_384
DISPLAY_OFF_SCRIPT_INTERPRETER = "bash"
DISPLAY_OFF_SCRIPT = r"""set -u
echo "SKELETON_DISPLAY_OFF_REQUEST_ACCEPTED=true"
applied=false
if command -v xset >/dev/null 2>&1; then
  if xset dpms force off >/dev/null 2>&1; then
    applied=true
  fi
fi
echo "SKELETON_DISPLAY_OFF_APPLIED=${applied}"
observable=false
state=unknown
if command -v xset >/dev/null 2>&1; then
  dpms="$(xset q 2>/dev/null || true)"
  if printf '%s\n' "$dpms" | grep -Eq 'Monitor is (On|Off|Standby|Suspend)'; then
    observable=true
    if printf '%s\n' "$dpms" | grep -Eq 'Monitor is (Off|Standby|Suspend)'; then
      state=off
    else
      state=on
    fi
  fi
fi
echo "SKELETON_DISPLAY_OFF_OBSERVABLE=${observable}"
echo "SKELETON_DISPLAY_OFF_STATE=${state}"
"""


@dataclass(frozen=True)
class DisplayPowerOffReceipt:
    request_accepted: bool
    applied: bool
    physically_verified: bool
    physical_verification: str
    success_criteria: str
    receipt_status: str
    exit_code: int | None
    request_id: str
    authority: Mapping[str, str]


SignerInvoker = Callable[[DisplayOffAuthority], Mapping[str, Any]]


def build_display_off_request(*, timestamp: str, nonce: str) -> dict[str, Any]:
    return {
        "schema": EXEC_REQUEST_SCHEMA,
        "request_id": DISPLAY_OFF_REQUEST_ID,
        "node_id": DEFAULT_NODE_ID,
        "argv": [],
        "environment": {},
        "timeout_seconds": DISPLAY_OFF_TIMEOUT_SECONDS,
        "execution_lane": ExecutionLane.PRIVILEGED_MUTATION.value,
        "operator_approval_ref": AUTHORITY_OPERATOR_APPROVAL,
        "idempotency_key": DISPLAY_OFF_IDEMPOTENCY_KEY,
        "run_as": ExecutionUser.ROOT.value,
        "mode": ExecMode.SCRIPT.value,
        "script": DISPLAY_OFF_SCRIPT,
        "script_interpreter": DISPLAY_OFF_SCRIPT_INTERPRETER,
        "timestamp": timestamp,
        "nonce": nonce,
        "max_output_bytes": DISPLAY_OFF_MAX_OUTPUT_BYTES,
        "public": True,
    }


def execute_display_power_off_task(
    body: str,
    *,
    signer_invoker: SignerInvoker | None = None,
    transport: Any | None = None,
) -> DisplayPowerOffReceipt:
    authority = validate_display_off_authority(body)
    signed_envelope = (signer_invoker or invoke_fixed_controller_signer)(authority)
    signed_request = revalidate_signed_display_off_envelope(signed_envelope, authority)
    receipt = execute_home_edge_request(signed_request, transport=transport)
    return classify_display_power_off_receipt(receipt, authority)


def invoke_fixed_controller_signer(authority: DisplayOffAuthority) -> Mapping[str, Any]:
    stdin = json.dumps({"authority": authority.to_mapping()}, sort_keys=True, separators=(",", ":")) + "\n"
    if len(stdin.encode("utf-8")) > MAX_SIGNER_STDIN_BYTES:
        raise HomeEdgeExecError("display-off signer input exceeds bounded limit")
    completed = subprocess.run(
        ["/usr/bin/sudo", "--non-interactive", "--", str(FIXED_SIGNER_PATH)],
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=SIGNER_TIMEOUT_SECONDS,
        check=False,
        env=_minimal_signer_environment(),
    )
    if completed.returncode != 0:
        raise HomeEdgeExecError("display-off signer rejected request")
    try:
        decoded = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise HomeEdgeExecError("display-off signer returned invalid JSON") from exc
    if not isinstance(decoded, Mapping):
        raise HomeEdgeExecError("display-off signer envelope must be an object")
    return decoded


def revalidate_signed_display_off_envelope(
    envelope: Mapping[str, Any],
    authority: DisplayOffAuthority,
) -> dict[str, Any]:
    if envelope.get("schema") != SIGNED_ENVELOPE_SCHEMA:
        raise HomeEdgeExecError("display-off signed envelope schema mismatch")
    if envelope.get("authority") != authority.to_mapping():
        raise HomeEdgeExecError("display-off signed envelope authority mismatch")
    request = envelope.get("request")
    if not isinstance(request, Mapping):
        raise HomeEdgeExecError("display-off signed envelope missing request")
    parsed = HomeEdgeExecRequest.from_mapping(request)
    expected = build_display_off_request(
        timestamp=parsed.timestamp or "",
        nonce=parsed.nonce or "",
    )
    expected["signature"] = parsed.signature
    if parsed.to_mapping() != expected:
        raise HomeEdgeExecError("display-off signed request is not exact")
    return parsed.to_mapping()


def classify_display_power_off_receipt(
    receipt: HomeEdgeExecReceipt,
    authority: DisplayOffAuthority,
) -> DisplayPowerOffReceipt:
    fields = _stdout_fields(receipt.stdout)
    request_accepted = fields.get("SKELETON_DISPLAY_OFF_REQUEST_ACCEPTED") == "true"
    applied = fields.get("SKELETON_DISPLAY_OFF_APPLIED") == "true"
    observable = fields.get("SKELETON_DISPLAY_OFF_OBSERVABLE") == "true"
    off_state = fields.get("SKELETON_DISPLAY_OFF_STATE") == "off"
    physically_verified = observable and off_state
    physical_verification = "met" if physically_verified else ("unobservable" if not observable else "not_off")
    success = request_accepted and applied and physically_verified
    return DisplayPowerOffReceipt(
        request_accepted=request_accepted,
        applied=applied,
        physically_verified=physically_verified,
        physical_verification=physical_verification,
        success_criteria="met" if success else "not_met",
        receipt_status=receipt.status,
        exit_code=receipt.exit_code,
        request_id=receipt.request_id,
        authority=authority.to_mapping(),
    )


def receipt_status_lines(receipt: DisplayPowerOffReceipt) -> list[str]:
    return [
        f"maintenance_task_id={DISPLAY_POWER_OFF_TASK_ID}",
        f"request_id={receipt.request_id}",
        f"request_accepted={str(receipt.request_accepted).lower()}",
        f"applied={str(receipt.applied).lower()}",
        f"physically_verified={str(receipt.physically_verified).lower()}",
        f"physical_verification={receipt.physical_verification}",
        f"receipt_status={receipt.receipt_status}",
        f"exit_code={receipt.exit_code if receipt.exit_code is not None else 'none'}",
    ]


def success_criteria_met(receipt: DisplayPowerOffReceipt) -> bool:
    return receipt.success_criteria == "met"


def _minimal_signer_environment() -> dict[str, str]:
    env: dict[str, str] = {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
    }
    if "HOME" in os.environ:
        env["HOME"] = os.environ["HOME"]
    return env


def _stdout_fields(stdout: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in stdout.splitlines():
        if not line.startswith("SKELETON_DISPLAY_OFF_") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        fields[name] = value.strip()
    return fields


def new_nonce() -> str:
    return f"display-off-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}"
