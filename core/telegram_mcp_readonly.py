from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

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

READONLY_TELEGRAM_TOOLS = (
    LIST_ALLOWED_SOURCES_TOOL,
    RESOLVE_SOURCE_TOOL,
    GET_HISTORY_TOOL,
    GET_MESSAGE_TOOL,
    SEARCH_TOOL,
)

DEFAULT_SOURCES_PATH = Path("/etc/skeleton/telegram_sources.yaml")
DEFAULT_STORE_PATH = Path("/var/lib/skeleton/telegram_gateway.sqlite3")


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
    )


@dataclass(frozen=True)
class TelegramReadonlyMcpDispatcher:
    gateway: TelegramGateway

    @classmethod
    def production(
        cls,
        *,
        sources_path: Path = DEFAULT_SOURCES_PATH,
        store_path: Path = DEFAULT_STORE_PATH,
    ) -> "TelegramReadonlyMcpDispatcher":
        loaded = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, Mapping) or not isinstance(loaded.get("sources"), list):
            raise ValueError("TELEGRAM_SOURCES_CONFIG_INVALID")
        return cls(gateway=TelegramGateway.for_sources(loaded["sources"], store_path=store_path))

    def list_tools(self) -> tuple[dict[str, object], ...]:
        return tool_descriptions()

    def call_tool(self, name: str, arguments: Mapping[str, object]) -> dict[str, object]:
        if not isinstance(arguments, Mapping):
            return _blocked("INVALID_ARGUMENTS", tool=name)
        try:
            if name == LIST_ALLOWED_SOURCES_TOOL:
                result = self.gateway.list_allowed_sources()
            elif name == RESOLVE_SOURCE_TOOL:
                result = self.gateway.resolve_source(
                    _required_str(arguments, "source_ref"),
                    mode=_mode(arguments),
                )
            elif name == GET_HISTORY_TOOL:
                source_ref = _required_str(arguments, "source_ref")
                mode = _mode(arguments)
                result = self.gateway.get_history(
                    source_ref,
                    mode=mode,
                    limit=self._bounded_limit(source_ref, mode, _optional_int(arguments, "limit")),
                    offset_id=_optional_int(arguments, "offset_id") or 0,
                    min_date=_optional_str(arguments, "min_date"),
                    max_date=_optional_str(arguments, "max_date"),
                )
            elif name == GET_MESSAGE_TOOL:
                result = self.gateway.get_message(
                    _required_str(arguments, "source_ref"),
                    _required_int(arguments, "message_id"),
                    mode=_mode(arguments),
                )
            elif name == SEARCH_TOOL:
                result = self.gateway.search(
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

    def _bounded_limit(self, source_ref: str, mode: TelegramAccessMode, limit: int | None) -> int | None:
        try:
            source = self.gateway.allowlist.resolve(source_ref, mode=mode)
        except TelegramGatewayError:
            return limit
        return source.bounded_limit(limit)


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


def _safe_reason(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"_", "-", ":", " "} else "_" for char in value)[:160]


def tool_names() -> tuple[str, ...]:
    return READONLY_TELEGRAM_TOOLS


def is_readonly_telegram_tool(name: str) -> bool:
    return name in READONLY_TELEGRAM_TOOLS
