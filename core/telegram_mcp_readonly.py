from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from core.telegram_gateway import TelegramGateway
from core.telegram_permissions import TelegramAccessMode, TelegramGatewayError


MCP_SCHEMA = "skeleton.telegram_mcp_readonly.v1"
SERVER_NAME = "skeleton-telegram-readonly"
SERVER_VERSION = "0.1.0"

LIST_ALLOWED_SOURCES_TOOL = "telegram_list_allowed_sources"
RESOLVE_SOURCE_TOOL = "telegram_resolve_source"
GET_HISTORY_TOOL = "telegram_get_history"
GET_MESSAGE_TOOL = "telegram_get_message"
SEARCH_TOOL = "telegram_search"
BACKEND_STATUS_TOOL = "telegram_backend_status"

READONLY_TELEGRAM_TOOLS = (
    LIST_ALLOWED_SOURCES_TOOL,
    RESOLVE_SOURCE_TOOL,
    GET_HISTORY_TOOL,
    GET_MESSAGE_TOOL,
    SEARCH_TOOL,
    BACKEND_STATUS_TOOL,
)

RUNTIME_BIND_CHILD_ISSUE = {
    "title": "Bind global Telegram read-only MCP to canonical reader runtime",
    "body": (
        "Bind core.telegram_mcp_readonly to the single production Telegram reader runtime. "
        "Do not create a second TelegramGateway, TelegramStore, MTProto session, or local source loader in the Hetzner MCP path. "
        "Expose the existing runtime through the TelegramReadonlyBackend protocol and verify unbound calls fail closed with BACKEND_UNAVAILABLE."
    ),
    "labels": ["runner:ready", "telegram", "mcp", "runtime-bind"],
}


class TelegramReadonlyBackend(Protocol):
    def list_allowed_sources(self) -> dict[str, object]: ...

    def resolve_source(self, source_ref: str, *, mode: TelegramAccessMode | str = TelegramAccessMode.READ_PUBLIC) -> dict[str, object]: ...

    def get_history(
        self,
        source_ref: str,
        *,
        mode: TelegramAccessMode | str = TelegramAccessMode.READ_PUBLIC,
        limit: int | None = None,
        offset_id: int = 0,
        min_date: str | None = None,
        max_date: str | None = None,
    ) -> dict[str, object]: ...

    def get_message(
        self,
        source_ref: str,
        message_id: int,
        *,
        mode: TelegramAccessMode | str = TelegramAccessMode.READ_PUBLIC,
    ) -> dict[str, object]: ...

    def search(
        self,
        query: str,
        *,
        source_ref: str | None = None,
        mode: TelegramAccessMode | str = TelegramAccessMode.READ_PUBLIC,
        limit: int = 20,
        min_date: str | None = None,
        max_date: str | None = None,
        semantic: bool = False,
    ) -> dict[str, object]: ...


@dataclass(frozen=True)
class TelegramGatewayReadonlyBackend:
    gateway: TelegramGateway

    def list_allowed_sources(self) -> dict[str, object]:
        return self.gateway.list_allowed_sources()

    def resolve_source(self, source_ref: str, *, mode: TelegramAccessMode | str = TelegramAccessMode.READ_PUBLIC) -> dict[str, object]:
        return self.gateway.resolve_source(source_ref, mode=mode)

    def get_history(
        self,
        source_ref: str,
        *,
        mode: TelegramAccessMode | str = TelegramAccessMode.READ_PUBLIC,
        limit: int | None = None,
        offset_id: int = 0,
        min_date: str | None = None,
        max_date: str | None = None,
    ) -> dict[str, object]:
        return self.gateway.get_history(
            source_ref,
            mode=mode,
            limit=self._bounded_limit(source_ref, TelegramAccessMode(mode), limit),
            offset_id=offset_id,
            min_date=min_date,
            max_date=max_date,
        )

    def get_message(
        self,
        source_ref: str,
        message_id: int,
        *,
        mode: TelegramAccessMode | str = TelegramAccessMode.READ_PUBLIC,
    ) -> dict[str, object]:
        return self.gateway.get_message(source_ref, message_id, mode=mode)

    def search(
        self,
        query: str,
        *,
        source_ref: str | None = None,
        mode: TelegramAccessMode | str = TelegramAccessMode.READ_PUBLIC,
        limit: int = 20,
        min_date: str | None = None,
        max_date: str | None = None,
        semantic: bool = False,
    ) -> dict[str, object]:
        return self.gateway.search(
            query,
            source_ref=source_ref,
            mode=mode,
            limit=limit,
            min_date=min_date,
            max_date=max_date,
            semantic=semantic,
        )

    def _bounded_limit(self, source_ref: str, mode: TelegramAccessMode, limit: int | None) -> int | None:
        try:
            source = self.gateway.allowlist.resolve(source_ref, mode=mode)
        except TelegramGatewayError:
            return limit
        return source.bounded_limit(limit)


_canonical_backend: TelegramReadonlyBackend | None = None


def bind_canonical_reader_runtime(backend: TelegramReadonlyBackend | None) -> None:
    global _canonical_backend
    _canonical_backend = backend


def canonical_reader_runtime() -> TelegramReadonlyBackend | None:
    return _canonical_backend


def tool_descriptions() -> tuple[dict[str, object], ...]:
    mode_schema = {"enum": [TelegramAccessMode.READ_PUBLIC.value, TelegramAccessMode.READ_ALLOWED_PRIVATE.value]}
    source_ref_schema = {"type": "string", "minLength": 1, "maxLength": 256}
    limit_schema = {"type": "integer", "minimum": 1, "maximum": 100}
    date_schema = {"type": "string", "minLength": 1, "maxLength": 64}
    return (
        {
            "name": LIST_ALLOWED_SOURCES_TOOL,
            "description": "List configured Telegram sources through the canonical TelegramGateway allowlist.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
        },
        {
            "name": RESOLVE_SOURCE_TOOL,
            "description": "Resolve one allowlisted Telegram source or peer through the canonical TelegramGateway.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source_ref"],
                "properties": {
                    "source_ref": source_ref_schema,
                    "mode": mode_schema,
                },
            },
        },
        {
            "name": GET_HISTORY_TOOL,
            "description": "Read bounded Telegram history for one allowlisted source through TelegramGateway.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source_ref"],
                "properties": {
                    "source_ref": source_ref_schema,
                    "mode": mode_schema,
                    "limit": limit_schema,
                    "offset_id": {"type": "integer", "minimum": 0},
                    "min_date": date_schema,
                    "max_date": date_schema,
                },
            },
        },
        {
            "name": GET_MESSAGE_TOOL,
            "description": "Read one cached or bounded-fetch Telegram message through TelegramGateway.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source_ref", "message_id"],
                "properties": {
                    "source_ref": source_ref_schema,
                    "message_id": {"type": "integer", "minimum": 1},
                    "mode": mode_schema,
                },
            },
        },
        {
            "name": SEARCH_TOOL,
            "description": "Search the TelegramGateway store with optional allowlisted source/date filters.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 512},
                    "source_ref": source_ref_schema,
                    "mode": mode_schema,
                    "limit": limit_schema,
                    "min_date": date_schema,
                    "max_date": date_schema,
                    "semantic": {"type": "boolean"},
                },
            },
        },
        {
            "name": BACKEND_STATUS_TOOL,
            "description": "Report whether the read-only Telegram MCP is bound to the canonical reader runtime.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
        },
    )


@dataclass(frozen=True)
class TelegramReadonlyMcpDispatcher:
    backend: TelegramReadonlyBackend | None = None

    @classmethod
    def production(cls) -> "TelegramReadonlyMcpDispatcher":
        return cls(backend=canonical_reader_runtime())

    @classmethod
    def for_gateway(cls, gateway: TelegramGateway) -> "TelegramReadonlyMcpDispatcher":
        return cls(backend=TelegramGatewayReadonlyBackend(gateway))

    def list_tools(self) -> tuple[dict[str, object], ...]:
        return tool_descriptions()

    def call_tool(self, name: str, arguments: Mapping[str, object]) -> dict[str, object]:
        if not isinstance(arguments, Mapping):
            return _blocked("INVALID_ARGUMENTS", tool=name)
        if name == BACKEND_STATUS_TOOL:
            return {"schema": MCP_SCHEMA, "tool": name, "result": self._backend_status()}
        if not is_readonly_telegram_tool(name):
            return _blocked("UNSUPPORTED_TOOL", tool=name)
        if self.backend is None:
            return _backend_unavailable(tool=name)
        try:
            if name == LIST_ALLOWED_SOURCES_TOOL:
                result = self.backend.list_allowed_sources()
            elif name == RESOLVE_SOURCE_TOOL:
                result = self.backend.resolve_source(
                    _required_str(arguments, "source_ref"),
                    mode=_mode(arguments),
                )
            elif name == GET_HISTORY_TOOL:
                result = self.backend.get_history(
                    _required_str(arguments, "source_ref"),
                    mode=_mode(arguments),
                    limit=_optional_int(arguments, "limit"),
                    offset_id=_optional_int(arguments, "offset_id") or 0,
                    min_date=_optional_str(arguments, "min_date"),
                    max_date=_optional_str(arguments, "max_date"),
                )
            elif name == GET_MESSAGE_TOOL:
                result = self.backend.get_message(
                    _required_str(arguments, "source_ref"),
                    _required_int(arguments, "message_id"),
                    mode=_mode(arguments),
                )
            elif name == SEARCH_TOOL:
                result = self.backend.search(
                    _required_str(arguments, "query"),
                    source_ref=_optional_str(arguments, "source_ref"),
                    mode=_mode(arguments),
                    limit=_optional_int(arguments, "limit") or 20,
                    min_date=_optional_str(arguments, "min_date"),
                    max_date=_optional_str(arguments, "max_date"),
                    semantic=_optional_bool(arguments, "semantic") or False,
                )
            else:
                return _blocked("UNSUPPORTED_TOOL", tool=name)
        except ValueError as exc:
            return _blocked(str(exc), tool=name)
        return {"schema": MCP_SCHEMA, "tool": name, "result": result}

    def _backend_status(self) -> dict[str, object]:
        if self.backend is not None:
            return {"status": "DONE", "backend_bound": True}
        return {
            "status": "blocked",
            "reason": "BACKEND_UNAVAILABLE",
            "backend_bound": False,
            "runtime_bind_child_issue": RUNTIME_BIND_CHILD_ISSUE,
        }


def _mode(arguments: Mapping[str, object]) -> TelegramAccessMode:
    value = arguments.get("mode", TelegramAccessMode.READ_PUBLIC.value)
    if value not in {TelegramAccessMode.READ_PUBLIC.value, TelegramAccessMode.READ_ALLOWED_PRIVATE.value}:
        raise ValueError("MODE_NOT_ALLOWED")
    return TelegramAccessMode(str(value))


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
        raise ValueError(f"{key.upper()}_INVALID")
    return value


def _required_int(arguments: Mapping[str, object], key: str) -> int:
    value = arguments.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key.upper()}_REQUIRED")
    return value


def _optional_int(arguments: Mapping[str, object], key: str) -> int | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key.upper()}_INVALID")
    return value


def _optional_bool(arguments: Mapping[str, object], key: str) -> bool | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{key.upper()}_INVALID")
    return value


def _blocked(reason: str, *, tool: str) -> dict[str, object]:
    return {
        "schema": MCP_SCHEMA,
        "tool": tool,
        "result": {"status": "blocked", "reason": _safe_reason(reason)},
    }


def _backend_unavailable(*, tool: str) -> dict[str, object]:
    return {
        "schema": MCP_SCHEMA,
        "tool": tool,
        "result": {
            "status": "blocked",
            "reason": "BACKEND_UNAVAILABLE",
            "runtime_bind_child_issue": RUNTIME_BIND_CHILD_ISSUE,
        },
    }


def _safe_reason(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"_", "-", ":", " "} else "_" for char in value)[:160]


def tool_names() -> tuple[str, ...]:
    return READONLY_TELEGRAM_TOOLS


def is_readonly_telegram_tool(name: str) -> bool:
    return name in READONLY_TELEGRAM_TOOLS
