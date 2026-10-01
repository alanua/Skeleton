from __future__ import annotations

from pathlib import Path

from core.hetzner_control_mcp import ACTION_GATE_TOOL, RUNNER_PRIVILEGED_TOOL, HetznerControlMcpDispatcher
from core.telegram_gateway import TelegramGateway, TelegramMessage
from core.telegram_mcp_readonly import (
    GET_HISTORY_TOOL,
    GET_MESSAGE_TOOL,
    LIST_ALLOWED_SOURCES_TOOL,
    MCP_SCHEMA,
    RESOLVE_SOURCE_TOOL,
    SEARCH_TOOL,
    TelegramReadonlyMcpDispatcher,
    tool_names,
)
from core.telegram_permissions import TelegramAllowlist, TelegramSource
from core.telegram_store import TelegramStore


def _source(**overrides: object) -> TelegramSource:
    values = {
        "source_id": "midnight",
        "handle": "@midnightquantum",
        "access_modes": ["READ_PUBLIC", "READ_ALLOWED_PRIVATE", "WRITE_BOT"],
        "secret_refs": ["telegram_api_id", "telegram_api_hash", "telegram_bot_token", "telegram_mtproto_string_session"],
        "allowlisted_peer_ids": ["@midnightquantum"],
        "max_page_size": 2,
        "operator_secret_route": "home_edge_devices_secrets_tab_to_bitwarden",
    }
    values.update(overrides)
    return TelegramSource.from_mapping(values)


def _dispatcher(tmp_path: Path) -> TelegramReadonlyMcpDispatcher:
    src = _source()
    gateway = TelegramGateway(
        allowlist=TelegramAllowlist({src.source_id: src}),
        store=TelegramStore.open(tmp_path / "telegram.db"),
    )
    gateway.read_public_seed(
        "midnight",
        [
            TelegramMessage(source_id="midnight", peer_id="@midnightquantum", message_id=1, text="alpha launch"),
            TelegramMessage(source_id="midnight", peer_id="@midnightquantum", message_id=2, text="beta update"),
        ],
    )
    return TelegramReadonlyMcpDispatcher(gateway=gateway)


class FakeFacade:
    def __init__(self) -> None:
        self.reads: list[tuple[str, int | None, int]] = []

    def read_messages(self, peer_id: str, *, limit=None, offset_id: int = 0, public: bool = True, **_kwargs):
        self.reads.append((peer_id, limit, offset_id))
        return [
            {
                "source_id": "midnight",
                "peer_id": peer_id,
                "message_id": 3,
                "text": "gamma live",
            }
        ]


def test_readonly_mcp_exposes_only_canonical_gateway_read_tools() -> None:
    tools = TelegramReadonlyMcpDispatcher(
        gateway=TelegramGateway(
            allowlist=TelegramAllowlist({"midnight": _source()}),
            store=TelegramStore.open(":memory:"),
        )
    ).list_tools()

    assert tuple(tool["name"] for tool in tools) == tool_names()
    assert "telegram_sync_source" not in tool_names()
    assert "telegram_watch_source" not in tool_names()
    assert "telegram_write_bot" not in tool_names()
    assert "telegram_propose_memory_fact" not in tool_names()

    exposed = {name for tool in tools for name in tool["inputSchema"].get("properties", {})}
    assert "argv" not in exposed
    assert "shell" not in exposed
    assert "env" not in exposed
    assert "secret" not in exposed
    assert "session" not in exposed
    assert "store_path" not in exposed
    assert "sources_path" not in exposed


def test_readonly_mcp_delegates_to_existing_gateway_store_and_allowlist(tmp_path: Path) -> None:
    dispatcher = _dispatcher(tmp_path)

    listed = dispatcher.call_tool(LIST_ALLOWED_SOURCES_TOOL, {})
    assert listed["schema"] == MCP_SCHEMA
    assert listed["result"]["sources"][0]["source_id"] == "midnight"

    resolved = dispatcher.call_tool(RESOLVE_SOURCE_TOOL, {"source_ref": "@midnightquantum"})
    assert resolved["result"]["status"] == "BLOCKED"
    assert resolved["result"]["reason_code"] == "AUTH_REQUIRED"

    message = dispatcher.call_tool(GET_MESSAGE_TOOL, {"source_ref": "@midnightquantum", "message_id": 1})
    assert message["result"]["message"]["text"] == "alpha launch"

    search = dispatcher.call_tool(SEARCH_TOOL, {"query": "beta", "source_ref": "@midnightquantum"})
    assert [message["message_id"] for message in search["result"]["messages"]] == [2]


def test_readonly_history_uses_canonical_gateway_live_facade_and_store(tmp_path: Path) -> None:
    src = _source()
    fake = FakeFacade()
    gateway = TelegramGateway(
        allowlist=TelegramAllowlist({src.source_id: src}),
        store=TelegramStore.open(tmp_path / "telegram.db"),
        mtproto_factory=lambda _source: fake,
    )
    dispatcher = TelegramReadonlyMcpDispatcher(gateway=gateway)

    history = dispatcher.call_tool(GET_HISTORY_TOOL, {"source_ref": "@midnightquantum", "limit": 99})

    assert history["result"]["status"] == "DONE"
    assert fake.reads == [("@midnightquantum", 2, 0)]
    assert history["result"]["messages"][0]["text"] == "gamma live"
    assert gateway.store.get_message(source_id="midnight", peer_id="@midnightquantum", message_id=3)["text"] == "gamma live"


def test_readonly_mcp_rejects_write_modes_and_unknown_tools_before_gateway(tmp_path: Path) -> None:
    dispatcher = _dispatcher(tmp_path)

    write_mode = dispatcher.call_tool(GET_HISTORY_TOOL, {"source_ref": "@midnightquantum", "mode": "WRITE_BOT"})
    assert write_mode["result"] == {"status": "blocked", "reason": "MODE_NOT_ALLOWED"}

    unknown = dispatcher.call_tool("telegram_write_bot", {"source_id": "midnight", "message": "nope"})
    assert unknown["result"] == {"status": "blocked", "reason": "UNSUPPORTED_TOOL"}


def test_hetzner_mcp_composes_existing_control_tools_and_global_telegram_readonly(tmp_path: Path) -> None:
    active = HetznerControlMcpDispatcher(
        privileged_gateway=type("NoGateway", (), {"submit": lambda self, request: (1, b"{}")})(),
        telegram_readonly=_dispatcher(tmp_path),
    )

    names = [tool["name"] for tool in active.list_tools()]
    assert names[:2] == [ACTION_GATE_TOOL, RUNNER_PRIVILEGED_TOOL]
    assert names[2:] == list(tool_names())

    result = active.call_tool(SEARCH_TOOL, {"query": "alpha", "source_ref": "@midnightquantum"})
    assert result["schema"] == MCP_SCHEMA
    assert result["result"]["messages"][0]["message_id"] == 1


def test_hetzner_production_keeps_control_tools_when_telegram_registration_is_unavailable(monkeypatch) -> None:
    from core import hetzner_control_mcp as hetzner

    monkeypatch.setattr(hetzner.TelegramReadonlyMcpDispatcher, "production", classmethod(lambda cls: (_ for _ in ()).throw(RuntimeError("bad config"))))
    monkeypatch.setattr(hetzner, "LocalSudoGatewayTransport", lambda: type("NoGateway", (), {"submit": lambda self, request: (1, b"{}")})())

    active = hetzner.HetznerControlMcpDispatcher.production()

    assert active.telegram_readonly is None
    assert [tool["name"] for tool in active.list_tools()] == [ACTION_GATE_TOOL, RUNNER_PRIVILEGED_TOOL]
