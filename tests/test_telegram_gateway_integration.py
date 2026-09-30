from __future__ import annotations

from pathlib import Path

from core.telegram_gateway import TelegramGateway
from core.telegram_mtproto import TelegramMTProtoFacade
from core.telegram_permissions import TelegramAllowlist, TelegramSource
from core.telegram_store import TelegramStore


class FakePublicMessage:
    def __init__(self, message_id: int, text: str) -> None:
        self.id = message_id
        self.message = text


class FakePublicClient:
    def iter_messages(self, peer_id: str, *, limit: int, offset_id: int = 0, min_id: int = 0):
        assert peer_id == "@midnightquantum"
        return [
            FakePublicMessage(1, "midnight quantum public note"),
            FakePublicMessage(2, "bounded public integration"),
        ][:limit]


def public_source() -> TelegramSource:
    return TelegramSource.from_mapping(
        {
            "source_id": "midnight",
            "handle": "@midnightquantum",
            "access_modes": ["READ_PUBLIC"],
            "secret_refs": ["telegram_api_id", "telegram_api_hash", "telegram_mtproto_string_session"],
            "operator_secret_route": "home_edge_devices_secrets_tab_to_bitwarden",
            "max_page_size": 5,
        }
    )


def test_midnightquantum_harness_is_bounded_and_auth_aware(tmp_path: Path) -> None:
    source = TelegramSource.from_mapping(
        {
            "source_id": "midnight",
            "handle": "@midnightquantum",
            "access_modes": ["READ_ALLOWED_PRIVATE"],
            "secret_refs": ["telegram_api_id", "telegram_api_hash"],
            "allowlisted_peer_ids": ["@midnightquantum"],
            "operator_secret_route": "home_edge_devices_secrets_tab_to_bitwarden",
        }
    )
    gateway = TelegramGateway(allowlist=TelegramAllowlist({"midnight": source}), store=TelegramStore.open(tmp_path / "telegram.db"))

    assert gateway.run_public_integration_harness("midnight") == {"status": "AUTH_REQUIRED", "source": "@midnightquantum"}


def test_midnightquantum_harness_reads_stores_and_searches_with_injected_client(tmp_path: Path) -> None:
    source = public_source()
    gateway = TelegramGateway(allowlist=TelegramAllowlist({"midnight": source}), store=TelegramStore.open(tmp_path / "telegram.db"))

    result = gateway.run_public_integration_harness(
        "midnight",
        mtproto=TelegramMTProtoFacade(source, client_factory=lambda: FakePublicClient()),
        query="integration",
    )

    assert result["status"] == "DONE"
    assert [message["message_id"] for message in result["messages"]] == [2, 1]
    assert result["messages"][0]["permalink"] == "https://t.me/midnightquantum/2"
    assert [message["message_id"] for message in result["search"]] == [2]


def test_midnightquantum_harness_does_not_attempt_live_login(tmp_path: Path) -> None:
    source = TelegramSource.from_mapping(
        {
            "source_id": "midnight",
            "handle": "@midnightquantum",
            "access_modes": ["READ_ALLOWED_PRIVATE"],
            "secret_refs": ["telegram_api_id", "telegram_api_hash", "telegram_mtproto_string_session"],
            "allowlisted_peer_ids": ["@midnightquantum"],
            "operator_secret_route": "home_edge_devices_secrets_tab_to_bitwarden",
        }
    )
    gateway = TelegramGateway(allowlist=TelegramAllowlist({"midnight": source}), store=TelegramStore.open(tmp_path / "telegram.db"))

    assert gateway.run_public_integration_harness("midnight") == {"status": "AUTH_REQUIRED", "source": "@midnightquantum"}
