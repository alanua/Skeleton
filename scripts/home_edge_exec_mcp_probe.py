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
from pathlib import Path
from typing import Any


DEFAULT_LAUNCHER = "/usr/local/bin/skeleton-home-edge-exec-mcp"
EXPECTED_TOOL = "home_edge_exec"


class McpProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProbeResult:
    initialized: bool
    tool_listed: bool
    call_status: str | None
    receipt_hash: str | None
    latency_ms: int | None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": "skeleton.home_edge.mcp_probe.v1",
            "status": "ok" if self.initialized and self.tool_listed and self.call_status in {None, "ok"} else "blocked",
            "initialized": self.initialized,
            "tool_listed": self.tool_listed,
            "call_status": self.call_status,
            "receipt_hash": self.receipt_hash,
            "latency_ms": self.latency_ms,
        }


def _send(process: subprocess.Popen[str], message: dict[str, Any]) -> None:
    if process.stdin is None:
        raise McpProbeError("MCP stdin is unavailable")
    process.stdin.write(json.dumps(message, sort_keys=True, separators=(",", ":")) + "\n")
    process.stdin.flush()


def _receive(process: subprocess.Popen[str], *, timeout_seconds: float) -> dict[str, Any]:
    if process.stdout is None:
        raise McpProbeError("MCP stdout is unavailable")
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        events = selector.select(timeout_seconds)
    finally:
        selector.close()
    if not events:
        raise McpProbeError("MCP response timed out")
    line = process.stdout.readline()
    if not line:
        raise McpProbeError("MCP server exited before returning a response")
    try:
        decoded = json.loads(line)
    except json.JSONDecodeError as exc:
        raise McpProbeError("MCP server returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise McpProbeError("MCP response must be an object")
    return decoded


def _expect_result(response: dict[str, Any], message_id: int) -> dict[str, Any]:
    if response.get("id") != message_id:
        raise McpProbeError("MCP response id mismatch")
    if "error" in response:
        raise McpProbeError("MCP request was rejected")
    result = response.get("result")
    if not isinstance(result, dict):
        raise McpProbeError("MCP response result is missing")
    return result


def run_probe(
    command: list[str],
    *,
    timeout_seconds: float = 10.0,
    perform_call: bool = True,
) -> ProbeResult:
    if not command:
        raise McpProbeError("MCP launcher command is empty")
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
                    "clientInfo": {"name": "skeleton-home-edge-probe", "version": "1.0"},
                },
            },
        )
        initialized = _expect_result(_receive(process, timeout_seconds=timeout_seconds), 1)
        if initialized.get("protocolVersion") != "2024-11-05":
            raise McpProbeError("MCP protocol version mismatch")

        _send(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listed = _expect_result(_receive(process, timeout_seconds=timeout_seconds), 2)
        tools = listed.get("tools")
        if not isinstance(tools, list) or EXPECTED_TOOL not in {item.get("name") for item in tools if isinstance(item, dict)}:
            raise McpProbeError("home_edge_exec tool is not registered")

        if not perform_call:
            return ProbeResult(True, True, None, None, None)

        started = time.monotonic()
        _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": EXPECTED_TOOL,
                    "arguments": {
                        "node_id": "home-edge-01",
                        "execution_lane": "read_only",
                        "run_as": "desktop-user",
                        "mode": "argv",
                        "argv": ["/usr/bin/true"],
                        "timeout_seconds": 5,
                        "public": True,
                    },
                },
            },
        )
        call = _expect_result(_receive(process, timeout_seconds=timeout_seconds), 3)
        latency_ms = int((time.monotonic() - started) * 1000)
        content = call.get("content")
        if not isinstance(content, list) or not content or not isinstance(content[0], dict):
            raise McpProbeError("MCP tool result content is missing")
        text = content[0].get("text")
        if not isinstance(text, str):
            raise McpProbeError("MCP tool result is not text")
        try:
            receipt = json.loads(text)
        except json.JSONDecodeError as exc:
            raise McpProbeError("MCP tool receipt is invalid JSON") from exc
        if not isinstance(receipt, dict):
            raise McpProbeError("MCP tool receipt must be an object")
        return ProbeResult(
            True,
            True,
            str(receipt.get("status")),
            str(receipt.get("receipt_hash")) if receipt.get("receipt_hash") else None,
            latency_ms,
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
    parser = argparse.ArgumentParser(description="Probe the direct Home Edge stdio MCP path.")
    parser.add_argument("--launcher", default=DEFAULT_LAUNCHER, help="Shell-like launcher command string.")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--skip-call", action="store_true", help="Validate initialize and tools/list only.")
    args = parser.parse_args(argv)

    try:
        result = run_probe(
            shlex.split(args.launcher),
            timeout_seconds=args.timeout_seconds,
            perform_call=not args.skip_call,
        )
        print(json.dumps(result.to_mapping(), sort_keys=True, separators=(",", ":")))
        return 0 if result.to_mapping()["status"] == "ok" else 2
    except Exception as exc:  # noqa: BLE001 - probe output must remain public-safe.
        print(
            json.dumps(
                {
                    "schema": "skeleton.home_edge.mcp_probe.v1",
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
