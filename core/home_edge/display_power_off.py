from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from uuid import uuid4

from .controller_auth import (
    DISPLAY_POWER_OFF_OPERATOR_APPROVAL,
    DISPLAY_POWER_OFF_TARGET_NODE,
    validate_display_power_off_authority_header,
)
from .executor_gateway import execute_home_edge_request


DISPLAY_POWER_OFF_SIGNER = "/usr/local/bin/skeleton-home-edge-display-off-controller-signer"
DISPLAY_POWER_OFF_IDEMPOTENCY_KEY = "display-off-literal-risk-field-20260809-v1"


def execute_display_power_off_task(
    body: str,
    *,
    registered_clean_main_sha: str,
    github_main_sha: str,
) -> dict[str, object]:
    validate_display_power_off_authority_header(body)
    if registered_clean_main_sha != github_main_sha:
        raise ValueError("trusted_runtime_sha_mismatch")

    unsigned_request = {
        "request_id": f"home-edge-display-off-{uuid4()}",
        "node_id": DISPLAY_POWER_OFF_TARGET_NODE,
        "execution_lane": "routine_mutation",
        "argv": ["xset", "dpms", "force", "off"],
        "timeout_seconds": 10,
        "operator_approval_ref": DISPLAY_POWER_OFF_OPERATOR_APPROVAL,
        "idempotency_key": DISPLAY_POWER_OFF_IDEMPOTENCY_KEY,
        "run_as": "desktop-user",
        "timestamp": datetime.now(UTC).isoformat(),
        "nonce": str(uuid4()),
        "public": True,
    }
    signed_request = sign_display_power_off_request(unsigned_request)
    receipt = execute_home_edge_request(signed_request)
    return {
        "status": receipt.status,
        "node_id": receipt.node_id,
        "exit_code": receipt.exit_code,
        "idempotency": receipt.idempotency,
        "receipt_hash": receipt.receipt_hash,
        "physically_verified": receipt.status == "ok" and receipt.exit_code == 0,
    }


def sign_display_power_off_request(request: dict[str, object]) -> dict[str, object]:
    completed = subprocess.run(
        ["sudo", "--non-interactive", "--", DISPLAY_POWER_OFF_SIGNER],
        input=json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("display_power_off_signer_failed")
    try:
        signed = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("display_power_off_signer_invalid_json") from exc
    if not isinstance(signed, dict) or not isinstance(signed.get("signature"), str):
        raise ValueError("display_power_off_signer_invalid_json")
    return signed


def receipt_status_lines(receipt: dict[str, object]) -> list[str]:
    return [
        "display_power_status=controller_request_sent",
        f"node_id={receipt.get('node_id', DISPLAY_POWER_OFF_TARGET_NODE)}",
        f"exit_code={receipt.get('exit_code')}",
        f"idempotency={receipt.get('idempotency', 'unknown')}",
        f"executor_receipt_hash={receipt.get('receipt_hash', 'unknown')}",
        f"physically_verified={str(receipt.get('physically_verified') is True).lower()}",
        "external_side_effects_executed=true",
    ]


def success_criteria_met(receipt: dict[str, object]) -> bool:
    return (
        receipt.get("status") == "ok"
        and receipt.get("exit_code") == 0
        and receipt.get("physically_verified") is True
    )
