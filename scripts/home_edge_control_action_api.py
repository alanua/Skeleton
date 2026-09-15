#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.home_edge_control_mcp import _execute_media, _handle_control

API_KEY_ENV = "SKELETON_HOME_MEDIA_ACTION_API_KEY"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 4096
RATE_WINDOW_SECONDS = 60
RATE_LIMIT = 30
_REQUEST_TIMES: deque[float] = deque()


def _openapi_document(server_url: str | None = None) -> dict[str, Any]:
    document: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {
            "title": "Skeleton Home Media Control",
            "version": "1.0.0",
            "description": (
                "Bounded control of the registered Home Edge media modes and default audio volume. "
                "No arbitrary commands, hosts, paths, or environment variables are accepted."
            ),
        },
        "paths": {
            "/v1/media/status": {
                "get": {
                    "operationId": "getHomeMediaStatus",
                    "summary": "Read the current home media mode and audio volume",
                    "responses": {
                        "200": {
                            "description": "Current bounded media state",
                            "content": {
                                "application/json": {"schema": {"$ref": "#/components/schemas/MediaResult"}}
                            },
                        }
                    },
                    "security": [{"bearerAuth": []}],
                }
            },
            "/v1/media/control": {
                "post": {
                    "operationId": "controlHomeMedia",
                    "summary": "Set a registered home media mode and/or default audio volume",
                    "description": (
                        "Use only after an explicit user request. Modes map to the existing trusted shortcuts: "
                        "chrome=Super+Alt+1, android_tv=Super+Alt+2, vlc=Super+Alt+3, "
                        "kiosk=Super+Alt+4, off=Super+Alt+0."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/MediaControl"}}
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Sanitized control receipt and readback",
                            "content": {
                                "application/json": {"schema": {"$ref": "#/components/schemas/MediaResult"}}
                            },
                        },
                        "400": {"description": "Invalid bounded request"},
                    },
                    "security": [{"bearerAuth": []}],
                }
            },
        },
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer"}
            },
            "schemas": {
                "MediaControl": {
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": ["chrome", "android_tv", "vlc", "kiosk", "off"],
                        },
                        "volume_percent": {"type": "integer", "minimum": 0, "maximum": 100},
                        "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 200},
                    },
                    "anyOf": [{"required": ["mode"]}, {"required": ["volume_percent"]}],
                    "additionalProperties": False,
                },
                "MediaResult": {
                    "type": "object",
                    "properties": {
                        "schema": {"type": "string"},
                        "status": {"type": "string", "enum": ["ok", "blocked"]},
                        "action": {"type": "string", "enum": ["status", "control"]},
                        "requested_mode": {"type": ["string", "null"]},
                        "selected_mode": {"type": ["string", "null"]},
                        "active_mode_hint": {"type": "string"},
                        "resolved_shortcut": {"type": ["string", "null"]},
                        "volume_percent": {"type": "integer"},
                        "muted": {"type": "boolean"},
                        "available_modes": {"type": "array", "items": {"type": "string"}},
                        "receipt_hash": {"type": ["string", "null"]},
                        "duration_seconds": {"type": ["number", "null"]},
                    },
                    "additionalProperties": True,
                },
            },
        },
    }
    if server_url:
        document["servers"] = [{"url": server_url.rstrip("/")}]
    return document


def _api_key() -> str:
    value = os.environ.get(API_KEY_ENV, "").strip()
    if len(value) < 32:
        raise RuntimeError("bounded action API key is not configured")
    return value


def _allowed_now() -> bool:
    now = time.monotonic()
    while _REQUEST_TIMES and now - _REQUEST_TIMES[0] > RATE_WINDOW_SECONDS:
        _REQUEST_TIMES.popleft()
    if len(_REQUEST_TIMES) >= RATE_LIMIT:
        return False
    _REQUEST_TIMES.append(now)
    return True


def _safe_error(exc: Exception) -> dict[str, str]:
    digest = hashlib.sha256(f"{type(exc).__name__}:{exc}".encode()).hexdigest()[:16]
    return {"status": "blocked", "error": type(exc).__name__, "error_ref": digest}


class Handler(BaseHTTPRequestHandler):
    server_version = "SkeletonHomeMedia/1.0"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/health":
            self._write_json(HTTPStatus.OK, {"status": "ok", "service": "skeleton-home-media-action"})
            return
        if self.path == "/openapi.json":
            self._write_json(HTTPStatus.OK, _openapi_document())
            return
        if self.path != "/v1/media/status":
            self._write_json(HTTPStatus.NOT_FOUND, {"status": "blocked", "error": "not_found"})
            return
        if not self._authorized():
            return
        if not _allowed_now():
            self._write_json(HTTPStatus.TOO_MANY_REQUESTS, {"status": "blocked", "error": "rate_limited"})
            return
        try:
            payload = _execute_media(status_only=True, mode=None, volume_percent=None, idempotency_key=None)
            self._write_json(HTTPStatus.OK, payload)
        except Exception as exc:  # noqa: BLE001 - bounded public error.
            self._write_json(HTTPStatus.BAD_GATEWAY, _safe_error(exc))

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path != "/v1/media/control":
            self._write_json(HTTPStatus.NOT_FOUND, {"status": "blocked", "error": "not_found"})
            return
        if not self._authorized():
            return
        if not _allowed_now():
            self._write_json(HTTPStatus.TOO_MANY_REQUESTS, {"status": "blocked", "error": "rate_limited"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ValueError("invalid request size")
            raw = self.rfile.read(length)
            decoded = json.loads(raw)
            if not isinstance(decoded, dict):
                raise ValueError("request body must be an object")
            payload = _handle_control(decoded)
            self._write_json(HTTPStatus.OK, payload)
        except (ValueError, json.JSONDecodeError) as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, _safe_error(exc))
        except Exception as exc:  # noqa: BLE001 - bounded public error.
            self._write_json(HTTPStatus.BAD_GATEWAY, _safe_error(exc))

    def _authorized(self) -> bool:
        expected = f"Bearer {_api_key()}"
        actual = self.headers.get("Authorization", "")
        if not hmac.compare_digest(actual, expected):
            self._write_json(
                HTTPStatus.UNAUTHORIZED,
                {"status": "blocked", "error": "unauthorized"},
                extra_headers={"WWW-Authenticate": "Bearer"},
            )
            return False
        return True

    def _write_json(
        self,
        status: HTTPStatus,
        payload: dict[str, Any],
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the bounded Home Edge media Action API on localhost.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--print-openapi", action="store_true")
    parser.add_argument("--server-url")
    args = parser.parse_args(argv)
    if args.print_openapi:
        print(json.dumps(_openapi_document(args.server_url), indent=2, sort_keys=True))
        return 0
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit("Action API must bind only to localhost")
    _api_key()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
