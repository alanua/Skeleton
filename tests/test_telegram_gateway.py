from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.telegram_gateway import TelegramGateway, TelegramMessage
from core.telegram_mtproto import TelegramMTProtoFacade
from core.telegram_permissions import TelegramAccessMode, TelegramAllowlist, TelegramSource
from core.telegram_store import TelegramStore


def source(**overrides: object) -> TelegramSource:
    values = {
        "source_id": "midnight",
        "handle": "@midnightquantum",
        "access_modes": ["READ_PUBLIC", "READ_ALLOWED_PRIVATE", "WRITE_BOT"],
        "secret_refs": ["telegram_api_id", "telegram_api_hash", "telegram_bot_token", "telegram_mtproto_string_session"],
        "allowlisted_peer_ids": ["@midnightquantum"],
        "max_page_size": 2,
        "operator_secret_route": "home_edge_devices_secrets_tab_to_bitwarden",
        "media_downloads_default": False,
    }
    values.update(overrides)
    return TelegramSource.from_mapping(values)


def gateway(tmp_path: Path, tg_source: TelegramSource | None = None) -> TelegramGateway:
    src = tg_source or source()
    return TelegramGateway(allowlist=TelegramAllowlist({src.source_id: src}), store=TelegramStore.open(tmp_path / "telegram.db"))


@dataclass
class FakeMessage:
    id: int
    message: str
    media: object | None = None


class FakeClient:
    def iter_messages(self, peer_id: str, *, limit: int, offset_id: int = 0):
        assert peer_id == "@midnightquantum"
        assert limit == 2
        return [FakeMessage(offset_id + 1, "alpha launch"), FakeMessage(offset_id + 2, "beta update", media=object())]


def test_read_allowed_private_syncs_with_bounded_resume_and_lazy_media(tmp_path: Path) -> None:
    gw = gateway(tmp_path)
    facade = TelegramMTProtoFacade(source(), client_factory=lambda: FakeClient())

    result = gw.read_allowed_private("midnight", "@midnightquantum", limit=99, offset_id=40, mtproto=facade)

    assert result["status"] == "DONE"
    assert result["resume_offset"] == 42
    assert gw.store.latest_message_id(source_id="midnight", peer_id="@midnightquantum") == 42
    stored = gw.store.search("beta")
    assert stored[0]["media"] == {"kind": "object", "downloaded": False}


def test_public_seed_is_idempotent_and_fts_searches(tmp_path: Path) -> None:
    gw = gateway(tmp_path)
    message = TelegramMessage(source_id="midnight", peer_id="@midnightquantum", message_id=7, text="searchable fact")

    assert gw.read_public_seed("midnight", [message]) == {"status": "DONE", "count": 1}
    assert gw.read_public_seed("midnight", [message]) == {"status": "DONE", "count": 1}

    assert [row["message_id"] for row in gw.store.search("searchable")] == [7]


def test_tombstone_hides_deleted_messages_from_search(tmp_path: Path) -> None:
    gw = gateway(tmp_path)
    gw.read_public_seed("midnight", [TelegramMessage(source_id="midnight", peer_id="@midnightquantum", message_id=8, text="gone soon")])
    gw.store.tombstone(source_id="midnight", peer_id="@midnightquantum", message_id=8, deleted_at="2026-09-26T00:00:00Z")

    assert gw.store.search("gone") == []


def test_write_bot_delegates_to_existing_sender(tmp_path: Path) -> None:
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def opener(request, *, timeout):
        calls.append((request.full_url, timeout))
        return Response()

    result = gateway(tmp_path).write_bot(
        "midnight",
        "hello",
        env={"SKELETON_TG_BOT": "token", "SKELETON_TG_CHAT": "chat"},
        opener=opener,
    )

    assert result == {"status": "DONE"}
    assert calls == [("https://api.telegram.org/bottoken/sendMessage", 10)]


def test_access_modes_are_exact() -> None:
    assert {mode.value for mode in TelegramAccessMode} == {"READ_PUBLIC", "READ_ALLOWED_PRIVATE", "WRITE_BOT"}
