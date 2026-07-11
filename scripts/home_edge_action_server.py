#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.home_edge.action_gateway import (  # noqa: E402
    HomeEdgeActionGateway,
    HomeEdgeGatewayError,
)


DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8765


def make_handler(gateway: HomeEdgeActionGateway) -> type[BaseHTTPRequestHandler]:
    class HomeEdgeActionHandler(BaseHTTPRequestHandler):
        server_version = "HomeEdgeActionGateway/1"

        def do_GET(self) -> None:
            if self.path == "/health":
                self._write_json(401, gateway.public_unauthenticated_status())
                return
            self._write_json(404, {"status": "not_found"})

        def do_POST(self) -> None:
            length = int(self.headers.get("content-length", "0"))
            body = self.rfile.read(min(length, 64 * 1024))
            key_id = self.headers.get("x-home-edge-key-id")
            signature = self.headers.get("x-home-edge-signature")
            try:
                if self.path == "/mcp/tools/list":
                    gateway._authenticate(body, key_id=key_id, signature=signature)
                    self._write_json(200, _mcp_tools(gateway.authenticated_capabilities()))
                    return
                if self.path in {"/mcp/call", "/actions"}:
                    receipt = gateway.handle_json(
                        body,
                        key_id=key_id,
                        signature=signature,
                    )
                    self._write_json(200, receipt)
                    return
            except HomeEdgeGatewayError as exc:
                self._write_json(403, {"status": "rejected", "reason": str(exc)})
                return
            except Exception:
                self._write_json(500, {"status": "unverified", "reason": "internal_error"})
                return
            self._write_json(404, {"status": "not_found"})

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _write_json(self, status: int, payload: dict[str, Any]) -> None:
            rendered = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("cache-control", "no-store")
            self.send_header("content-length", str(len(rendered)))
            self.end_headers()
            self.wfile.write(rendered)

    return HomeEdgeActionHandler


def _mcp_tools(capabilities: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "skeleton.home_edge.mcp_tools.v1",
        "tools": [
            {
                "name": action.replace(".", "_"),
                "description": f"Execute {action} on home-edge-01",
                "input_schema": {
                    "type": "object",
                    "required": [
                        "node_id",
                        "action_id",
                        "request_id",
                        "timestamp",
                        "nonce",
                        "idempotency_key",
                    ],
                    "properties": {
                        "node_id": {"const": "home-edge-01"},
                        "action_id": {"const": action},
                        "request_id": {"type": "string"},
                        "timestamp": {"type": "string", "format": "date-time"},
                        "nonce": {"type": "string"},
                        "idempotency_key": {"type": "string"},
                        "parameters": {"type": "object"},
                    },
                    "additionalProperties": False,
                },
            }
            for action in capabilities["actions"]
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Private Home Edge direct action gateway.")
    parser.add_argument("--bind", default=DEFAULT_BIND)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    if args.bind in {"0.0.0.0", "::"}:
        print("refusing public wildcard bind; use loopback or a private Tailscale address", file=sys.stderr)
        return 2
    try:
        gateway = HomeEdgeActionGateway.from_environment()
    except HomeEdgeGatewayError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    server = ThreadingHTTPServer((args.bind, args.port), make_handler(gateway))
    print(json.dumps({"status": "listening", "bind": args.bind, "port": args.port}, sort_keys=True))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
