from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from core.action_gate import ActionGateRequest, validate_action_request
from core.awareness_context import (
    AWARENESS_CONTEXT_RECEIPT_SCHEMA,
    AWARENESS_CONTEXT_SCHEMA,
    PRIVACY_BOUNDARY,
)
from core.memory_bootstrap import (
    MEMORY_BOOTSTRAP_RESPONSE_SCHEMA,
    PRIVATE_CONTEXT_ENV,
    PRIVATE_CONTEXT_MARKER,
)
from core.private_memory_history import content_hash
from core.runner_controller_privileged_gateway import LocalSudoGatewayTransport


MCP_SCHEMA = "skeleton.hetzner_control_mcp.v1"
SERVER_NAME = "skeleton-control-hetzner"
SERVER_VERSION = "0.1.0"
ACTION_GATE_TOOL = "action_gate_validate"
RUNNER_PRIVILEGED_TOOL = "runner_controller_privileged_gateway_submit"
AWARENESS_CONTEXT_TOOL = "awareness_context_read"
_MAX_AWARENESS_HANDOFF_BYTES = 1024 * 1024
_MAX_TASK_BODY_CHARS = 8000
_MAX_REQUESTED_CAPABILITIES = 32


class PrivilegedGatewayTransport(Protocol):
    def submit(self, request: Mapping[str, object]) -> tuple[int, bytes]: ...


@dataclass(frozen=True)
class AwarenessContextReadRequest:
    project_id: str
    task_route: str
    task_body: str
    requested_capabilities: tuple[str, ...] = ()


class AwarenessContextProvider(Protocol):
    def read(self, request: AwarenessContextReadRequest) -> Mapping[str, object]: ...


class AwarenessContextProviderError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class PrivateHandoffAwarenessContextProvider:
    """Read the already-authorized private boot handoff; never open another store."""

    context_file_env: str = PRIVATE_CONTEXT_ENV

    def read(self, request: AwarenessContextReadRequest) -> Mapping[str, object]:
        raw_path = os.environ.get(self.context_file_env)
        if not isinstance(raw_path, str) or not raw_path:
            raise AwarenessContextProviderError("AWARENESS_PRIVATE_HANDOFF_UNAVAILABLE")
        path = Path(raw_path)
        if not path.is_absolute():
            raise AwarenessContextProviderError("AWARENESS_PRIVATE_HANDOFF_INVALID")
        try:
            metadata = os.lstat(path)
        except OSError as exc:
            raise AwarenessContextProviderError("AWARENESS_PRIVATE_HANDOFF_UNAVAILABLE") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_size > _MAX_AWARENESS_HANDOFF_BYTES
        ):
            raise AwarenessContextProviderError("AWARENESS_PRIVATE_HANDOFF_INVALID")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AwarenessContextProviderError("AWARENESS_PRIVATE_HANDOFF_INVALID") from exc
        if (
            not isinstance(payload, Mapping)
            or payload.get("schema") != MEMORY_BOOTSTRAP_RESPONSE_SCHEMA
            or payload.get("marker") != PRIVATE_CONTEXT_MARKER
        ):
            raise AwarenessContextProviderError("AWARENESS_PRIVATE_HANDOFF_INVALID")
        awareness = payload.get("awareness")
        if not isinstance(awareness, Mapping):
            raise AwarenessContextProviderError("AWARENESS_CONTEXT_UNAVAILABLE")
        packet = dict(awareness)
        _validate_awareness_packet(packet, request)
        return {
            "packet": packet,
            "receipt": _awareness_public_receipt(packet),
        }


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
        {
            "name": AWARENESS_CONTEXT_TOOL,
            "description": "Read the current private Awareness Context from the existing boot handoff without mutation.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["project_id", "task_route", "task_body"],
                "properties": {
                    "project_id": {"type": "string", "minLength": 1, "maxLength": 160},
                    "task_route": {"type": "string", "minLength": 1, "maxLength": 160},
                    "task_body": {"type": "string", "minLength": 1, "maxLength": _MAX_TASK_BODY_CHARS},
                    "requested_capabilities": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 160},
                        "maxItems": _MAX_REQUESTED_CAPABILITIES,
                        "uniqueItems": True,
                    },
                },
            },
        },
    )


@dataclass(frozen=True)
class HetznerControlMcpDispatcher:
    privileged_gateway: PrivilegedGatewayTransport
    awareness_provider: AwarenessContextProvider | None = None

    @classmethod
    def production(cls) -> "HetznerControlMcpDispatcher":
        return cls(
            privileged_gateway=LocalSudoGatewayTransport(),
            awareness_provider=PrivateHandoffAwarenessContextProvider(),
        )

    def list_tools(self) -> tuple[dict[str, object], ...]:
        return tool_descriptions()

    def call_tool(self, name: str, arguments: Mapping[str, object]) -> dict[str, object]:
        if not isinstance(arguments, Mapping):
            return _blocked("INVALID_ARGUMENTS")
        if name == ACTION_GATE_TOOL:
            return self._action_gate(arguments)
        if name == RUNNER_PRIVILEGED_TOOL:
            return self._runner_privileged_gateway(arguments)
        if name == AWARENESS_CONTEXT_TOOL:
            return self._awareness_context(arguments)
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

    def _awareness_context(self, arguments: Mapping[str, object]) -> dict[str, object]:
        if self.awareness_provider is None:
            return _blocked("AWARENESS_PROVIDER_UNAVAILABLE", tool=AWARENESS_CONTEXT_TOOL)
        try:
            project_id = _required_bounded_str(arguments, "project_id", 160)
            task_route = _required_bounded_str(arguments, "task_route", 160)
            task_body = _required_bounded_str(arguments, "task_body", _MAX_TASK_BODY_CHARS)
            requested_capabilities = tuple(
                _optional_bounded_str_list(
                    arguments,
                    "requested_capabilities",
                    max_items=_MAX_REQUESTED_CAPABILITIES,
                    max_item_chars=160,
                )
            )
            provided = self.awareness_provider.read(
                AwarenessContextReadRequest(
                    project_id=project_id,
                    task_route=task_route,
                    task_body=task_body,
                    requested_capabilities=requested_capabilities,
                )
            )
        except AwarenessContextProviderError as exc:
            return _blocked(exc.reason, tool=AWARENESS_CONTEXT_TOOL)
        except ValueError as exc:
            return _blocked(str(exc), tool=AWARENESS_CONTEXT_TOOL)
        except Exception:
            return _blocked("AWARENESS_PROVIDER_FAILED", tool=AWARENESS_CONTEXT_TOOL)
        packet = provided.get("packet")
        receipt = provided.get("receipt")
        if not isinstance(packet, Mapping) or not isinstance(receipt, Mapping):
            return _blocked("AWARENESS_PROVIDER_INVALID", tool=AWARENESS_CONTEXT_TOOL)
        return {
            "schema": MCP_SCHEMA,
            "tool": AWARENESS_CONTEXT_TOOL,
            "result": {
                "status": "DONE",
                "awareness": dict(packet),
                "receipt": dict(receipt),
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


def _validate_awareness_packet(
    packet: Mapping[str, object],
    request: AwarenessContextReadRequest,
) -> None:
    if (
        packet.get("schema") != AWARENESS_CONTEXT_SCHEMA
        or packet.get("privacy_boundary") != PRIVACY_BOUNDARY
        or packet.get("runtime_mutation_performed") is not False
        or packet.get("canonical_store_mutation_performed") is not False
    ):
        raise AwarenessContextProviderError("AWARENESS_CONTEXT_INVALID")
    if packet.get("project_id") != request.project_id or packet.get("task_route") != request.task_route:
        raise AwarenessContextProviderError("AWARENESS_CONTEXT_SCOPE_MISMATCH")
    awareness_hash = packet.get("awareness_hash")
    material = dict(packet)
    material.pop("awareness_hash", None)
    if not isinstance(awareness_hash, str) or content_hash(material) != awareness_hash:
        raise AwarenessContextProviderError("AWARENESS_CONTEXT_HASH_INVALID")
    requested = set(request.requested_capabilities)
    if requested:
        sections = packet.get("sections")
        capability_truth = sections.get("capability_truth") if isinstance(sections, Mapping) else None
        records = capability_truth.get("records") if isinstance(capability_truth, Mapping) else None
        present = {
            str(record.get("capability_id"))
            for record in records
            if isinstance(records, list) and isinstance(record, Mapping)
        } if isinstance(records, list) else set()
        if not requested.issubset(present):
            raise AwarenessContextProviderError("AWARENESS_CONTEXT_CAPABILITY_SCOPE_MISMATCH")


def _awareness_public_receipt(packet: Mapping[str, object]) -> dict[str, object]:
    sections = packet.get("sections")
    section_hashes: dict[str, str] = {}
    if isinstance(sections, Mapping):
        for name, value in sections.items():
            if isinstance(name, str) and isinstance(value, Mapping):
                section_hash = value.get("section_hash")
                if isinstance(section_hash, str):
                    section_hashes[name] = section_hash
    labels = packet.get("labels")
    return {
        "schema": AWARENESS_CONTEXT_RECEIPT_SCHEMA,
        "project_id": packet.get("project_id"),
        "task_route": packet.get("task_route"),
        "awareness_hash": packet.get("awareness_hash"),
        "checked_at": packet.get("checked_at"),
        "labels": dict(labels) if isinstance(labels, Mapping) else {},
        "section_hashes": dict(sorted(section_hashes.items())),
        "privacy_boundary": PRIVACY_BOUNDARY,
        "public_safe": True,
        "private_payloads_included": False,
        "runtime_mutation_performed": False,
        "canonical_store_mutation_performed": False,
    }


def _required_bounded_str(arguments: Mapping[str, object], key: str, max_chars: int) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value or len(value) > max_chars:
        raise ValueError(f"{key.upper()}_REQUIRED")
    return value


def _optional_bounded_str_list(
    arguments: Mapping[str, object],
    key: str,
    *,
    max_items: int,
    max_item_chars: int,
) -> list[str]:
    value = arguments.get(key, [])
    if (
        not isinstance(value, list)
        or len(value) > max_items
        or not all(
            isinstance(item, str) and item and len(item) <= max_item_chars
            for item in value
        )
        or len(set(value)) != len(value)
    ):
        raise ValueError(f"{key.upper()}_INVALID")
    return list(value)


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
