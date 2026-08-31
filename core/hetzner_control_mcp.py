from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from core.action_gate import ActionGateRequest, validate_action_request
from core.runner_controller_privileged_gateway import LocalSudoGatewayTransport


MCP_SCHEMA = "skeleton.hetzner_control_mcp.v1"
SERVER_NAME = "skeleton-control-hetzner"
SERVER_VERSION = "0.1.0"
ACTION_GATE_TOOL = "action_gate_validate"
RUNNER_PRIVILEGED_TOOL = "runner_controller_privileged_gateway_submit"


class PrivilegedGatewayTransport(Protocol):
    def submit(self, request: Mapping[str, object]) -> tuple[int, bytes]: ...


def tool_descriptions() -> tuple[dict[str, object], ...]:
    return (
        {
            "name": ACTION_GATE_TOOL,
            "description": "Validate one approved repository action through the existing ActionGate.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "action_type",
                    "repo",
                    "pr_number",
                    "expected_head_sha",
                    "expected_files",
                    "user_approved",
                ],
                "properties": {
                    "action_type": {"const": "merge_pull_request"},
                    "repo": {"const": "alanua/Skeleton"},
                    "pr_number": {"type": "integer", "minimum": 1},
                    "expected_head_sha": {"type": "string", "pattern": "^[0-9a-fA-F]{40}$"},
                    "expected_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "uniqueItems": True,
                    },
                    "user_approved": {"const": True},
                },
            },
        },
        {
            "name": RUNNER_PRIVILEGED_TOOL,
            "description": "Submit one exact runner-controller privileged gateway request through the installed gateway transport.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["request"],
                "properties": {
                    "request": {
                        "type": "object",
                        "additionalProperties": True,
                    }
                },
            },
        },
    )


@dataclass(frozen=True)
class HetznerControlMcpDispatcher:
    privileged_gateway: PrivilegedGatewayTransport

    @classmethod
    def production(cls) -> "HetznerControlMcpDispatcher":
        return cls(privileged_gateway=LocalSudoGatewayTransport())

    def list_tools(self) -> tuple[dict[str, object], ...]:
        return tool_descriptions()

    def call_tool(self, name: str, arguments: Mapping[str, object]) -> dict[str, object]:
        if not isinstance(arguments, Mapping):
            return _blocked("INVALID_ARGUMENTS")
        if name == ACTION_GATE_TOOL:
            return self._action_gate(arguments)
        if name == RUNNER_PRIVILEGED_TOOL:
            return self._runner_privileged_gateway(arguments)
        return _blocked("UNSUPPORTED_TOOL")

    def _action_gate(self, arguments: Mapping[str, object]) -> dict[str, object]:
        try:
            request = ActionGateRequest(
                action_type=_required_str(arguments, "action_type"),
                repo=_required_str(arguments, "repo"),
                pr_number=_required_int(arguments, "pr_number"),
                expected_head_sha=_required_str(arguments, "expected_head_sha"),
                expected_files=tuple(_required_str_list(arguments, "expected_files")),
                user_approved=_required_bool(arguments, "user_approved"),
            )
        except ValueError as exc:
            return _blocked(str(exc))
        decision = validate_action_request(request)
        return {
            "schema": MCP_SCHEMA,
            "tool": ACTION_GATE_TOOL,
            "result": {
                "status": decision.status,
                "action_type": decision.action_type,
                "repo": decision.repo,
                "pr_number": decision.pr_number,
                "reasons": list(decision.reasons),
            },
        }

    def _runner_privileged_gateway(self, arguments: Mapping[str, object]) -> dict[str, object]:
        request = arguments.get("request")
        if not isinstance(request, Mapping):
            return _blocked("REQUEST_OBJECT_REQUIRED", tool=RUNNER_PRIVILEGED_TOOL)
        code, stdout = self.privileged_gateway.submit(request)
        try:
            receipt = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _blocked("GATEWAY_RECEIPT_INVALID", tool=RUNNER_PRIVILEGED_TOOL)
        if not isinstance(receipt, Mapping):
            return _blocked("GATEWAY_RECEIPT_NOT_OBJECT", tool=RUNNER_PRIVILEGED_TOOL)
        return {
            "schema": MCP_SCHEMA,
            "tool": RUNNER_PRIVILEGED_TOOL,
            "result": {
                "status": str(receipt.get("status") or "blocked") if code == 0 else "blocked",
                "exit_code": code,
                "receipt": dict(receipt),
            },
        }


def handle_jsonrpc_message(
    message: Mapping[str, object],
    *,
    dispatcher: HetznerControlMcpDispatcher | None = None,
) -> dict[str, object] | None:
    active = dispatcher or HetznerControlMcpDispatcher.production()
    method = message.get("method")
    msg_id = message.get("id")
    try:
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            }
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": list(active.list_tools())}}
        if method == "tools/call":
            params = message.get("params") if isinstance(message.get("params"), Mapping) else {}
            name = params.get("name")
            arguments = params.get("arguments")
            if not isinstance(name, str):
                raise ValueError("tool name required")
            if not isinstance(arguments, Mapping):
                raise ValueError("tool arguments must be an object")
            result = active.call_tool(name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, sort_keys=True)}],
                    "isError": _is_error_result(result),
                },
            }
        raise ValueError(f"unsupported method: {method}")
    except Exception as exc:  # noqa: BLE001 - JSON-RPC boundary must fail closed.
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": _safe_error(exc)}}


def _blocked(reason: str, *, tool: str | None = None) -> dict[str, object]:
    return {
        "schema": MCP_SCHEMA,
        "tool": tool or "",
        "result": {
            "status": "blocked",
            "reason": _safe_reason(reason),
        },
    }


def _is_error_result(result: Mapping[str, object]) -> bool:
    payload = result.get("result")
    return isinstance(payload, Mapping) and payload.get("status") in {"blocked", "NEEDS_OPERATOR"}


def _required_str(arguments: Mapping[str, object], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key.upper()}_REQUIRED")
    return value


def _required_int(arguments: Mapping[str, object], key: str) -> int:
    value = arguments.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key.upper()}_REQUIRED")
    return value


def _required_bool(arguments: Mapping[str, object], key: str) -> bool:
    value = arguments.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key.upper()}_REQUIRED")
    return value


def _required_str_list(arguments: Mapping[str, object], key: str) -> list[str]:
    value = arguments.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key.upper()}_REQUIRED")
    return value


def _safe_error(exc: Exception) -> str:
    return _safe_reason(f"{type(exc).__name__}: {exc}")


def _safe_reason(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"_", "-", ":", " "} else "_" for char in value)[:160]
