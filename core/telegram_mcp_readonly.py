from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


MCP_SCHEMA = "skeleton.telegram_mcp_readonly.v1"
SERVER_NAME = "skeleton-telegram-readonly"
SERVER_VERSION = "0.1.0"

RESOLVE_SOURCE_TOOL = "resolve_source"
GET_HISTORY_TOOL = "get_history"
GET_MESSAGE_TOOL = "get_message"
SEARCH_TOOL = "search"
SYNC_SOURCE_TOOL = "sync_source"
LIST_ALLOWED_SOURCES_TOOL = "list_allowed_sources"

READ_MODES = ("READ_PUBLIC", "READ_ALLOWED_PRIVATE")
TELEGRAM_TOOL_NAMES = (
    RESOLVE_SOURCE_TOOL,
    GET_HISTORY_TOOL,
    GET_MESSAGE_TOOL,
    SEARCH_TOOL,
    SYNC_SOURCE_TOOL,
    LIST_ALLOWED_SOURCES_TOOL,
)


class TelegramReadonlyBackend(Protocol):
    def resolve_source(self, source_ref: str, *, mode: str = "READ_PUBLIC") -> dict[str, object]: ...

    def get_history(
        self,
        source_ref: str,
        *,
        mode: str = "READ_PUBLIC",
        limit: int | None = None,
        offset_id: int = 0,
        min_date: str | None = None,
        max_date: str | None = None,
    ) -> dict[str, object]: ...

    def get_message(self, source_ref: str, message_id: int, *, mode: str = "READ_PUBLIC") -> dict[str, object]: ...

    def search(
        self,
        query: str,
        *,
        source_ref: str | None = None,
        mode: str = "READ_PUBLIC",
        limit: int = 20,
        min_date: str | None = None,
        max_date: str | None = None,
        semantic: bool = False,
    ) -> dict[str, object]: ...

    def sync_source(
        self,
        source_ref: str,
        *,
        mode: str = "READ_PUBLIC",
        limit: int | None = None,
        overlap: int = 2,
    ) -> dict[str, object]: ...

    def list_allowed_sources(self) -> dict[str, object]: ...


class UnavailableTelegramReadonlyBackend:
    def resolve_source(self, source_ref: str, *, mode: str = "READ_PUBLIC") -> dict[str, object]:
        return _backend_unavailable()

    def get_history(
        self,
        source_ref: str,
        *,
        mode: str = "READ_PUBLIC",
        limit: int | None = None,
        offset_id: int = 0,
        min_date: str | None = None,
        max_date: str | None = None,
    ) -> dict[str, object]:
        return _backend_unavailable()

    def get_message(self, source_ref: str, message_id: int, *, mode: str = "READ_PUBLIC") -> dict[str, object]:
        return _backend_unavailable()

    def search(
        self,
        query: str,
        *,
        source_ref: str | None = None,
        mode: str = "READ_PUBLIC",
        limit: int = 20,
        min_date: str | None = None,
        max_date: str | None = None,
        semantic: bool = False,
    ) -> dict[str, object]:
        return _backend_unavailable()

    def sync_source(
        self,
        source_ref: str,
        *,
        mode: str = "READ_PUBLIC",
        limit: int | None = None,
        overlap: int = 2,
    ) -> dict[str, object]:
        return _backend_unavailable()

    def list_allowed_sources(self) -> dict[str, object]:
        return _backend_unavailable()


def tool_descriptions() -> tuple[dict[str, object], ...]:
    source_ref = {"type": "string", "minLength": 1}
    mode = {"type": "string", "enum": list(READ_MODES), "default": "READ_PUBLIC"}
    limit = {"type": "integer", "minimum": 1, "maximum": 100}
    date_filter = {"type": "string", "minLength": 1}
    return (
        {
            "name": RESOLVE_SOURCE_TOOL,
            "description": "Resolve one allowlisted Telegram source or private peer through the canonical Telegram gateway.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source_ref"],
                "properties": {"source_ref": source_ref, "mode": mode},
            },
        },
        {
            "name": GET_HISTORY_TOOL,
            "description": "Read bounded history for one allowlisted Telegram source through the canonical Telegram gateway.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source_ref"],
                "properties": {
                    "source_ref": source_ref,
                    "mode": mode,
                    "limit": limit,
                    "offset_id": {"type": "integer", "minimum": 0},
                    "min_date": date_filter,
                    "max_date": date_filter,
                },
            },
        },
        {
            "name": GET_MESSAGE_TOOL,
            "description": "Read one Telegram message already allowed by the canonical Telegram gateway.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source_ref", "message_id"],
                "properties": {
                    "source_ref": source_ref,
                    "message_id": {"type": "integer", "minimum": 1},
                    "mode": mode,
                },
            },
        },
        {
            "name": SEARCH_TOOL,
            "description": "Search the canonical Telegram local store with optional allowlisted-source filters.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "source_ref": source_ref,
                    "mode": mode,
                    "limit": limit,
                    "min_date": date_filter,
                    "max_date": date_filter,
                    "semantic": {"type": "boolean", "default": False},
                },
            },
        },
        {
            "name": SYNC_SOURCE_TOOL,
            "description": "Run the canonical bounded Telegram source sync for one allowlisted source.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source_ref"],
                "properties": {
                    "source_ref": source_ref,
                    "mode": mode,
                    "limit": limit,
                    "overlap": {"type": "integer", "minimum": 0, "maximum": 100},
                },
            },
        },
        {
            "name": LIST_ALLOWED_SOURCES_TOOL,
            "description": "List configured Telegram sources that the canonical gateway may access.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
        },
    )


@dataclass(frozen=True)
class TelegramReadonlyMcpDispatcher:
    backend: TelegramReadonlyBackend

    @classmethod
    def unavailable(cls) -> "TelegramReadonlyMcpDispatcher":
        return cls(backend=UnavailableTelegramReadonlyBackend())

    @classmethod
    def production(cls) -> "TelegramReadonlyMcpDispatcher":
        return cls.unavailable()

    def list_tools(self) -> tuple[dict[str, object], ...]:
        return tool_descriptions()

    def call_tool(self, name: str, arguments: Mapping[str, object]) -> dict[str, object]:
        if not isinstance(arguments, Mapping):
            return _blocked("INVALID_ARGUMENTS", tool=name)
        try:
            if name == RESOLVE_SOURCE_TOOL:
                result = self.backend.resolve_source(
                    _required_str(arguments, "source_ref"),
                    mode=_read_mode(arguments),
                )
            elif name == GET_HISTORY_TOOL:
                result = self.backend.get_history(
                    _required_str(arguments, "source_ref"),
                    mode=_read_mode(arguments),
                    limit=_optional_int(arguments, "limit", min_value=1, max_value=100),
                    offset_id=_optional_int(arguments, "offset_id", default=0, min_value=0) or 0,
                    min_date=_optional_str(arguments, "min_date"),
                    max_date=_optional_str(arguments, "max_date"),
                )
            elif name == GET_MESSAGE_TOOL:
                result = self.backend.get_message(
                    _required_str(arguments, "source_ref"),
                    _required_int(arguments, "message_id", min_value=1),
                    mode=_read_mode(arguments),
                )
            elif name == SEARCH_TOOL:
                result = self.backend.search(
                    _required_str(arguments, "query"),
                    source_ref=_optional_str(arguments, "source_ref"),
                    mode=_read_mode(arguments),
                    limit=_optional_int(arguments, "limit", default=20, min_value=1, max_value=100) or 20,
                    min_date=_optional_str(arguments, "min_date"),
                    max_date=_optional_str(arguments, "max_date"),
                    semantic=_optional_bool(arguments, "semantic", default=False),
                )
            elif name == SYNC_SOURCE_TOOL:
                result = self.backend.sync_source(
                    _required_str(arguments, "source_ref"),
                    mode=_read_mode(arguments),
                    limit=_optional_int(arguments, "limit", min_value=1, max_value=100),
                    overlap=_optional_int(arguments, "overlap", default=2, min_value=0, max_value=100) or 0,
                )
            elif name == LIST_ALLOWED_SOURCES_TOOL:
                if arguments:
                    return _blocked("UNSUPPORTED_ARGUMENTS", tool=name)
                result = self.backend.list_allowed_sources()
            else:
                return _blocked("UNSUPPORTED_TOOL", tool=name)
        except ValueError as exc:
            return _blocked(str(exc), tool=name)
        except Exception as exc:  # noqa: BLE001 - MCP boundary must fail closed.
            return _blocked(_safe_error(exc), tool=name)
        return {"schema": MCP_SCHEMA, "tool": name, "result": result}


def handle_jsonrpc_message(
    message: Mapping[str, object],
    *,
    dispatcher: TelegramReadonlyMcpDispatcher | None = None,
) -> dict[str, object] | None:
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
            tools = dispatcher.list_tools() if dispatcher is not None else tool_descriptions()
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": list(tools)}}
        if method == "tools/call":
            try:
                active = dispatcher or TelegramReadonlyMcpDispatcher.production()
            except Exception as exc:  # noqa: BLE001 - production setup must fail closed.
                result = _blocked(_safe_error(exc))
            else:
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


def _backend_unavailable() -> dict[str, object]:
    return {"status": "BLOCKED", "reason_code": "BACKEND_UNAVAILABLE"}


def _is_error_result(result: Mapping[str, object]) -> bool:
    payload = result.get("result")
    return isinstance(payload, Mapping) and payload.get("status") in {"blocked", "BLOCKED", "AUTH_REQUIRED", "FLOOD_WAIT"}


def _read_mode(arguments: Mapping[str, object]) -> str:
    value = arguments.get("mode", "READ_PUBLIC")
    if value not in READ_MODES:
        raise ValueError("READ_MODE_REQUIRED")
    return str(value)


def _required_str(arguments: Mapping[str, object], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key.upper()}_REQUIRED")
    return value


def _optional_str(arguments: Mapping[str, object], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key.upper()}_REQUIRED")
    return value


def _required_int(
    arguments: Mapping[str, object],
    key: str,
    *,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    value = arguments.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key.upper()}_REQUIRED")
    if min_value is not None and value < min_value:
        raise ValueError(f"{key.upper()}_OUT_OF_BOUNDS")
    if max_value is not None and value > max_value:
        raise ValueError(f"{key.upper()}_OUT_OF_BOUNDS")
    return value


def _optional_int(
    arguments: Mapping[str, object],
    key: str,
    *,
    default: int | None = None,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int | None:
    value = arguments.get(key, default)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key.upper()}_REQUIRED")
    if min_value is not None and value < min_value:
        raise ValueError(f"{key.upper()}_OUT_OF_BOUNDS")
    if max_value is not None and value > max_value:
        raise ValueError(f"{key.upper()}_OUT_OF_BOUNDS")
    return value


def _optional_bool(arguments: Mapping[str, object], key: str, *, default: bool) -> bool:
    value = arguments.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key.upper()}_REQUIRED")
    return value


def _safe_error(exc: Exception) -> str:
    return _safe_reason(f"{type(exc).__name__}: {exc}")


def _safe_reason(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"_", "-", ":", " "} else "_" for char in value)[:160]
