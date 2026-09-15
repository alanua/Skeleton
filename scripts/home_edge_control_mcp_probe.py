#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import selectors
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any

DEFAULT_LAUNCHER = "/usr/local/bin/skeleton-home-media-control-mcp"
EXPECTED_TOOLS = {"home_media_status", "home_media_control"}


class ProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProbeResult:
    initialized: bool
    tools: tuple[str, ...]
    status_call: str | None
    latency_ms: int | None
    receipt_hash: str | None

    def to_mapping(self) -> dict[str, Any]:
        ok = self.initialized and set(self.tools) == EXPECTED_TOOLS and self.status_call in {None, "ok"}
        return {
            "schema": "skeleton.home_media.mcp_probe.v1",
            "status": "ok" if ok else "blocked",
            "initialized": self.initialized,
            "tools": list(self.tools),
            "status_call": self.status_call,
            "latency_ms": self.latency_ms,
            "receipt_hash": self.receipt_hash,
        }


def _send(process: subprocess.Popen[str], message: dict[str, Any]) -> None:
    if process.stdin is None:
        raise ProbeError("MCP stdin is unavailable")
    process.stdin.write(json.dumps(message, sort_keys=True, separators=(",", ":")) + "\n")
    process.stdin.flush()


def _receive(process: subprocess.Popen[str], timeout_seconds: float) -> dict[str, Any]:
    if process.stdout is None:
        raise ProbeError("MCP stdout is unavailable")
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        events = selector.select(timeout_seconds)
    finally:
        selector.close()
    if not events:
        raise ProbeError("MCP response timed out")
    line = process.stdout.readline()
    if not line:
        raise ProbeError("MCP server exited before returning a response")
    try:
        decoded = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProbeError("MCP server returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise ProbeError("MCP response must be an object")
    return decoded


def _result(response: dict[str, Any], message_id: int) -> dict[str, Any]:
    if response.get("id") != message_id:
        raise ProbeError("MCP response id mismatch")
    if "error" in response:
        raise ProbeError("MCP request was rejected")
    result = response.get("result")
    if not isinstance(result, dict):
        raise ProbeError("MCP result is missing")
    return result


def run_probe(command: list[str], *, timeout_seconds: float = 10.0, call_status: bool = True) -> ProbeResult:
    if not command:
        raise ProbeError("MCP launcher command is empty")
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "skeleton-home-media-probe", "version": "1.0"},
                },
            },
        )
        initialized = _result(_receive(process, timeout_seconds), 1)
        if initialized.get("protocolVersion") != "2024-11-05":
            raise ProbeError("MCP protocol version mismatch")

        _send(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listed = _result(_receive(process, timeout_seconds), 2)
        tools = listed.get("tools")
        if not isinstance(tools, list):
            raise ProbeError("MCP tools list is missing")
        names = tuple(sorted(item.get("name") for item in tools if isinstance(item, dict) and isinstance(item.get("name"), str)))
        if set(names) != EXPECTED_TOOLS:
            raise ProbeError("bounded media tools do not match expected set")

        if not call_status:
            return ProbeResult(True, names, None, None, None)

        started = time.monotonic()
        _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "home_media_status", "arguments": {}},
            },
        )
        called = _result(_receive(process, timeout_seconds), 3)
        latency_ms = int((time.monotonic() - started) * 1000)
        structured = called.get("structuredContent")
        if not isinstance(structured, dict):
            raise ProbeError("MCP structured status result is missing")
        return ProbeResult(
            True,
            names,
            str(structured.get("status")),
            latency_ms,
            str(structured.get("receipt_hash")) if structured.get("receipt_hash") else None,
        )
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe the bounded Home Edge media MCP server.")
    parser.add_argument("--launcher", default=DEFAULT_LAUNCHER, help="Shell-like launcher command string.")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--skip-call", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_probe(
            shlex.split(args.launcher),
            timeout_seconds=args.timeout_seconds,
            call_status=not args.skip_call,
        )
        payload = result.to_mapping()
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 0 if payload["status"] == "ok" else 2
    except Exception as exc:  # noqa: BLE001 - public-safe health output.
        print(
            json.dumps(
                {
                    "schema": "skeleton.home_media.mcp_probe.v1",
                    "status": "blocked",
                    "error": type(exc).__name__,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
