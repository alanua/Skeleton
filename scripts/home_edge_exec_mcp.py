#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.home_edge.executor import HomeEdgeExecRequest, PUBLIC_ERROR_MESSAGE, sign_request
from core.home_edge.executor_gateway import EXEC_HMAC_SECRET_ENV
from core.home_edge.executor_gateway import execute_home_edge_request


TOOL_NAME = "home_edge_exec"


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        response = handle_message(json.loads(line))
        if response is not None:
            print(json.dumps(response, sort_keys=True, separators=(",", ":")), flush=True)
    return 0


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    msg_id = message.get("id")
    public_requested = False
    try:
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "skeleton-home-edge-exec", "version": "0.1.0"},
                },
            }
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": [_tool_description()]}}
        if method == "tools/call":
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            if params.get("name") != TOOL_NAME:
                raise ValueError("unknown tool")
            args = params.get("arguments")
            if not isinstance(args, dict):
                raise ValueError("tool arguments must be an object")
            public_requested = bool(args.get("public", False))
            receipt = execute_home_edge_request(_finalize_signed_request(args)).to_mapping()
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(receipt, sort_keys=True)}],
                    "isError": receipt["status"] not in {"ok"},
                },
            }
        raise ValueError(f"unsupported method: {method}")
    except Exception as exc:  # noqa: BLE001 - MCP errors are structured JSON-RPC errors.
        message_text = PUBLIC_ERROR_MESSAGE if public_requested else _public_safe_error(exc)
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": message_text}}


def _finalize_signed_request(args: dict[str, Any]) -> dict[str, Any]:
    if "signature" in args or "secret" in args or "hmac_secret" in args:
        raise ValueError("MCP callers must not provide signing material")
    secret = os.environ.get(EXEC_HMAC_SECRET_ENV, "").strip()
    if not secret:
        raise ValueError("private MCP signing secret is not configured")
    data = dict(args)
    data["request_id"] = data.get("request_id") or f"home-edge-exec-{uuid4()}"
    data["timestamp"] = datetime.now(UTC).isoformat()
    data["nonce"] = f"home-edge-exec-{uuid4()}"
    request = HomeEdgeExecRequest.from_mapping({key: value for key, value in data.items() if value is not None})
    outbound = request.to_mapping(include_signature=False)
    outbound["signature"] = sign_request(request, secret)
    return outbound


def _public_safe_error(exc: Exception) -> str:
    if str(exc) == PUBLIC_ERROR_MESSAGE:
        return PUBLIC_ERROR_MESSAGE
    return f"{type(exc).__name__}: {exc}"


def _tool_description() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": "Execute one operator-approved universal Home Edge argv or bounded script request.",
        "inputSchema": {
            "type": "object",
            "required": ["node_id", "execution_lane", "timeout_seconds"],
            "properties": {
                "request_id": {"type": "string"},
                "node_id": {"const": "home-edge-01"},
                "argv": {"type": "array", "items": {"type": "string"}},
                "stdin_text": {"type": "string"},
                "stdin_base64": {"type": "string"},
                "cwd": {"type": "string"},
                "environment": {"type": "object", "additionalProperties": {"type": "string"}},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 900},
                "execution_lane": {"enum": ["read_only", "routine_mutation", "privileged_mutation", "destructive"]},
                "operator_approval_ref": {"type": "string"},
                "idempotency_key": {"type": "string"},
                "run_as": {"enum": ["desktop-user", "root"]},
                "mode": {"enum": ["argv", "script"]},
                "script": {"type": "string"},
                "script_interpreter": {"enum": ["bash", "python3"]},
                "public": {"type": "boolean"},
            },
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
