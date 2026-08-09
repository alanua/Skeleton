#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.home_edge.controller_auth import (
    DISPLAY_POWER_OFF_OPERATOR_APPROVAL,
    DISPLAY_POWER_OFF_TARGET_NODE,
)
from core.home_edge.executor import HomeEdgeExecRequest, sign_request


SECRET_PATH = Path("/etc/skeleton/home-edge-display-off-controller.hmac")


def main() -> int:
    request = json.loads(sys.stdin.read())
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    if request.get("node_id") != DISPLAY_POWER_OFF_TARGET_NODE:
        raise ValueError("request node_id mismatch")
    if request.get("execution_lane") != "routine_mutation":
        raise ValueError("request lane mismatch")
    if request.get("operator_approval_ref") != DISPLAY_POWER_OFF_OPERATOR_APPROVAL:
        raise ValueError("request approval mismatch")
    if request.get("argv") != ["xset", "dpms", "force", "off"]:
        raise ValueError("request argv mismatch")
    secret = SECRET_PATH.read_text(encoding="utf-8").strip()
    if not secret:
        raise ValueError("signing secret missing")
    parsed = HomeEdgeExecRequest.from_mapping(request)
    request["signature"] = sign_request(parsed, secret)
    print(json.dumps(request, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
