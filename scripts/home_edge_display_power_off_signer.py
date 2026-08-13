#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path


SIGNED_ENVELOPE_SCHEMA = "skeleton.home_edge.display_power_off.signed_envelope.v1"
EXEC_REQUEST_SCHEMA = "skeleton.home_edge.exec_request.v1"
EXEC_HMAC_SECRET_ENV = "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET"
EXEC_HMAC_SECRET_CONFIG_PATH = Path("/etc/skeleton/home-edge-executor-controller.env")
MAX_STDIN_BYTES = 16_384
SIGNATURE_PREFIX = "sha256="
AUTHORITY = {
    "Mode": "RUNTIME_MAINTENANCE_TASK",
    "Maintenance Task ID": "home_edge_01_display_power_off_v1",
    "Risk": "yellow",
    "Target Node": "home-edge-01",
    "Operator Approval": "EXPLICIT_2026_08_09_TURN_OFF_HOME_EDGE_MONITOR",
    "Privacy Boundary": "PRIVATE_RUNTIME_STATE_PUBLIC_SAFE_STATUS",
}
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


def main(argv: list[str]) -> int:
    if argv:
        return _reject("argv not supported")
    raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    if len(raw) > MAX_STDIN_BYTES:
        return _reject("stdin too large")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _reject("invalid stdin")
    if not isinstance(decoded, dict) or decoded.get("authority") != AUTHORITY:
        return _reject("authority mismatch")

    secret = _read_hmac_secret()
    now = datetime.now(UTC).isoformat()
    nonce = f"display-off-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}"
    request = _request(now, nonce)
    request["signature"] = _sign_request(request, secret)
    envelope = {
        "schema": SIGNED_ENVELOPE_SCHEMA,
        "authority": AUTHORITY,
        "request": request,
    }
    print(json.dumps(envelope, sort_keys=True, separators=(",", ":")))
    return 0


def _request(timestamp: str, nonce: str) -> dict[str, object]:
    return {
        "schema": EXEC_REQUEST_SCHEMA,
        "request_id": "home-edge-01-display-power-off-v1",
        "node_id": "home-edge-01",
        "argv": [],
        "environment": {},
        "timeout_seconds": 30,
        "execution_lane": "privileged_mutation",
        "operator_approval_ref": "EXPLICIT_2026_08_09_TURN_OFF_HOME_EDGE_MONITOR",
        "idempotency_key": "home-edge-01-display-power-off-v1",
        "run_as": "root",
        "mode": "script",
        "script": DISPLAY_OFF_SCRIPT,
        "script_interpreter": "bash",
        "timestamp": timestamp,
        "nonce": nonce,
        "max_output_bytes": 16384,
        "public": True,
    }


def _sign_request(request: dict[str, object], secret: str) -> str:
    canonical = dict(request)
    canonical.pop("signature", None)
    canonical.pop("public", None)
    message = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return SIGNATURE_PREFIX + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _read_hmac_secret() -> str:
    value = os.environ.get(EXEC_HMAC_SECRET_ENV, "").strip()
    if value:
        return value
    if not EXEC_HMAC_SECRET_CONFIG_PATH.exists():
        raise RuntimeError("controller credential is unavailable")
    data = EXEC_HMAC_SECRET_CONFIG_PATH.read_text(encoding="utf-8")
    for line in data.splitlines():
        match = re.fullmatch(rf"{EXEC_HMAC_SECRET_ENV}=([A-Za-z0-9_./+=:@%-]+)", line.strip())
        if match is not None:
            return match.group(1)
    raise RuntimeError("controller credential is unavailable")


def _reject(message: str) -> int:
    print(json.dumps({"status": "blocked", "error": message}, sort_keys=True), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
