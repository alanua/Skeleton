from __future__ import annotations

import json

from core.telegram_mcp_readonly import (
    GET_HISTORY_TOOL,
    GET_MESSAGE_TOOL,
    LIST_ALLOWED_SOURCES_TOOL,
    RESOLVE_SOURCE_TOOL,
    SEARCH_TOOL,
    SYNC_SOURCE_TOOL,
    TELEGRAM_TOOL_NAMES,
    MCP_SCHEMA,
    TelegramReadonlyMcpDispatcher,
    handle_jsonrpc_message,
)


class CapturingTelegramBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def resolve_source(self, source_ref: str, *, mode: str = "READ_PUBLIC") -> dict[str, object]:
        self.calls.append(("resolve_source", (source_ref,), {"mode": mode}))
        return {"status": "DONE", "source": {"source_id": "midnight", "handle": "@midnightquantum"}}

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
        self.calls.append(
            (
                "get_history",
                (source_ref,),
                {"mode": mode, "limit": limit, "offset_id": offset_id, "min_date": min_date, "max_date": max_date},
            )
        )
        return {"status": "DONE", "messages": [{"message_id": 7, "text": "public fixture"}]}

    def get_message(self, source_ref: str, message_id: int, *, mode: str = "READ_PUBLIC") -> dict[str, object]:
        self.calls.append(("get_message", (source_ref, message_id), {"mode": mode}))
        return {"status": "DONE", "message": {"message_id": message_id, "text": "public fixture"}}

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
        self.calls.append(
            (
                "search",
                (query,),
                {
                    "source_ref": source_ref,
                    "mode": mode,
                    "limit": limit,
                    "min_date": min_date,
                    "max_date": max_date,
                    "semantic": semantic,
                },
            )
        )
        return {"status": "DONE", "messages": []}

    def sync_source(
        self,
        source_ref: str,
        *,
        mode: str = "READ_PUBLIC",
        limit: int | None = None,
        overlap: int = 2,
    ) -> dict[str, object]:
        self.calls.append(("sync_source", (source_ref,), {"mode": mode, "limit": limit, "overlap": overlap}))
        return {"status": "DONE", "count": 0, "deleted_count": 0, "cursor_message_id": 0, "messages": []}

    def list_allowed_sources(self) -> dict[str, object]:
        self.calls.append(("list_allowed_sources", (), {}))
        return {"status": "DONE", "sources": [{"source_id": "midnight", "handle": "@midnightquantum"}]}


def dispatcher() -> TelegramReadonlyMcpDispatcher:
    return TelegramReadonlyMcpDispatcher(backend=CapturingTelegramBackend())


def test_tools_are_exact_six_readonly_canonical_backend_facades() -> None:
    tools = dispatcher().list_tools()

    assert [tool["name"] for tool in tools] == [
        RESOLVE_SOURCE_TOOL,
        GET_HISTORY_TOOL,
        GET_MESSAGE_TOOL,
        SEARCH_TOOL,
        SYNC_SOURCE_TOOL,
        LIST_ALLOWED_SOURCES_TOOL,
    ]
    assert tuple(tool["name"] for tool in tools) == TELEGRAM_TOOL_NAMES
    assert len(tools) == 6

    exposed_properties = {
        property_name
        for tool in tools
        for property_name in tool["inputSchema"].get("properties", {})
    }
    assert "write_bot" not in {tool["name"] for tool in tools}
    assert "watch_source" not in {tool["name"] for tool in tools}
    assert "argv" not in exposed_properties
    assert "script" not in exposed_properties
    assert "shell" not in exposed_properties
    assert "secret" not in exposed_properties
    assert "mtproto" not in exposed_properties
    assert "env" not in exposed_properties


def test_tool_calls_delegate_to_canonical_backend_with_bounded_arguments() -> None:
    backend = CapturingTelegramBackend()
    active = TelegramReadonlyMcpDispatcher(backend=backend)

    assert active.call_tool(RESOLVE_SOURCE_TOOL, {"source_ref": "@midnightquantum", "mode": "READ_PUBLIC"})["result"]["status"] == "DONE"
    assert active.call_tool(
        GET_HISTORY_TOOL,
        {
            "source_ref": "@midnightquantum",
            "mode": "READ_ALLOWED_PRIVATE",
            "limit": 10,
            "offset_id": 4,
            "min_date": "2026-09-01T00:00:00Z",
            "max_date": "2026-09-30T00:00:00Z",
        },
    )["result"]["status"] == "DONE"
    assert active.call_tool(GET_MESSAGE_TOOL, {"source_ref": "@midnightquantum", "message_id": 7})["result"]["status"] == "DONE"
    assert active.call_tool(
        SEARCH_TOOL,
        {"query": "quantum", "source_ref": "@midnightquantum", "limit": 3, "semantic": True},
    )["result"]["status"] == "DONE"
    assert active.call_tool(SYNC_SOURCE_TOOL, {"source_ref": "@midnightquantum", "limit": 2, "overlap": 1})["result"]["status"] == "DONE"
    assert active.call_tool(LIST_ALLOWED_SOURCES_TOOL, {})["result"]["status"] == "DONE"

    assert backend.calls == [
        ("resolve_source", ("@midnightquantum",), {"mode": "READ_PUBLIC"}),
        (
            "get_history",
            ("@midnightquantum",),
            {
                "mode": "READ_ALLOWED_PRIVATE",
                "limit": 10,
                "offset_id": 4,
                "min_date": "2026-09-01T00:00:00Z",
                "max_date": "2026-09-30T00:00:00Z",
            },
        ),
        ("get_message", ("@midnightquantum", 7), {"mode": "READ_PUBLIC"}),
        (
            "search",
            ("quantum",),
            {
                "source_ref": "@midnightquantum",
                "mode": "READ_PUBLIC",
                "limit": 3,
                "min_date": None,
                "max_date": None,
                "semantic": True,
            },
        ),
        ("sync_source", ("@midnightquantum",), {"mode": "READ_PUBLIC", "limit": 2, "overlap": 1}),
        ("list_allowed_sources", (), {}),
    ]


def test_unsupported_or_write_shaped_calls_fail_closed_before_backend() -> None:
    backend = CapturingTelegramBackend()
    active = TelegramReadonlyMcpDispatcher(backend=backend)

    unsupported = active.call_tool("write_bot", {"source_id": "midnight", "message": "hello"})
    invalid_mode = active.call_tool(GET_HISTORY_TOOL, {"source_ref": "@midnightquantum", "mode": "WRITE_BOT"})
    invalid_limit = active.call_tool(SEARCH_TOOL, {"query": "quantum", "limit": 0})
    unexpected_list_arg = active.call_tool(LIST_ALLOWED_SOURCES_TOOL, {"secret": "x"})

    assert unsupported["result"] == {"status": "blocked", "reason": "UNSUPPORTED_TOOL"}
    assert invalid_mode["result"] == {"status": "blocked", "reason": "READ_MODE_REQUIRED"}
    assert invalid_limit["result"] == {"status": "blocked", "reason": "LIMIT_OUT_OF_BOUNDS"}
    assert unexpected_list_arg["result"] == {"status": "blocked", "reason": "UNSUPPORTED_ARGUMENTS"}
    assert backend.calls == []


def test_backend_blocked_status_marks_mcp_call_as_error() -> None:
    class BlockedBackend(CapturingTelegramBackend):
        def get_message(self, source_ref: str, message_id: int, *, mode: str = "READ_PUBLIC") -> dict[str, object]:
            return {"status": "BLOCKED", "reason_code": "AUTH_REQUIRED", "message": None}

    called = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": GET_MESSAGE_TOOL, "arguments": {"source_ref": "@midnightquantum", "message_id": 7}},
        },
        dispatcher=TelegramReadonlyMcpDispatcher(backend=BlockedBackend()),
    )

    assert called is not None
    assert called["result"]["isError"] is True
    payload = json.loads(called["result"]["content"][0]["text"])
    assert payload["schema"] == MCP_SCHEMA
    assert payload["result"]["reason_code"] == "AUTH_REQUIRED"


def test_production_backend_is_unbound_and_fails_closed_without_runtime_state() -> None:
    active = TelegramReadonlyMcpDispatcher.production()

    result = active.call_tool(SEARCH_TOOL, {"query": "quantum"})

    assert result["schema"] == MCP_SCHEMA
    assert result["tool"] == SEARCH_TOOL
    assert result["result"] == {"status": "BLOCKED", "reason_code": "BACKEND_UNAVAILABLE"}


def test_jsonrpc_without_injected_dispatcher_does_not_construct_local_runtime() -> None:
    called = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": LIST_ALLOWED_SOURCES_TOOL, "arguments": {}},
        },
    )

    assert called is not None
    assert called["result"]["isError"] is True
    payload = json.loads(called["result"]["content"][0]["text"])
    assert payload["result"] == {"status": "BLOCKED", "reason_code": "BACKEND_UNAVAILABLE"}


def test_jsonrpc_boundary_lists_and_calls_tools() -> None:
    active = dispatcher()

    initialized = handle_jsonrpc_message({"jsonrpc": "2.0", "id": 0, "method": "initialize"}, dispatcher=active)
    listed = handle_jsonrpc_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, dispatcher=active)
    called = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": SEARCH_TOOL, "arguments": {"query": "quantum"}},
        },
        dispatcher=active,
    )

    assert initialized is not None
    assert initialized["result"]["serverInfo"]["name"] == "skeleton-telegram-readonly"
    assert listed is not None
    assert [tool["name"] for tool in listed["result"]["tools"]] == [
        RESOLVE_SOURCE_TOOL,
        GET_HISTORY_TOOL,
        GET_MESSAGE_TOOL,
        SEARCH_TOOL,
        SYNC_SOURCE_TOOL,
        LIST_ALLOWED_SOURCES_TOOL,
    ]
    assert called is not None
    payload = json.loads(called["result"]["content"][0]["text"])
    assert payload["tool"] == SEARCH_TOOL
    assert payload["result"]["status"] == "DONE"
