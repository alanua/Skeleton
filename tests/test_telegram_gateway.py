from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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


class FakeFloodWait(Exception):
    seconds = 13


@dataclass
class FakeEntity:
    offset: int
    length: int
    url: str | None = None


@dataclass
class FakeSender:
    id: int
    username: str
    first_name: str
    last_name: str


@dataclass
class FakeReply:
    reply_to_msg_id: int
    reply_to_top_id: int


@dataclass
class FakeForward:
    from_name: str
    channel_id: int
    date: datetime


class RichFakeMessage:
    id = 101
    message = "alpha https://example.test launch"
    date = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    edit_date = datetime(2026, 9, 26, 13, 0, tzinfo=timezone.utc)
    sender = FakeSender(7, "operator", "Ada", "Lovelace")
    post_author = "Channel Admin"
    entities = [FakeEntity(6, 20)]
    reply_to = FakeReply(99, 12)
    fwd_from = FakeForward("Upstream", 12345, datetime(2026, 9, 25, tzinfo=timezone.utc))
    media = object()
    caption = "media caption"


class RichClient:
    def __init__(self, messages=None):
        self.messages = messages or [RichFakeMessage()]

    def iter_messages(self, peer_id: str, *, limit: int, offset_id: int = 0):
        return self.messages


class AsyncClient:
    def __init__(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    async def connect(self):
        self.connected = True

    async def is_user_authorized(self):
        return True

    async def iter_messages(self, peer_id: str, *, limit: int, offset_id: int = 0, min_id: int = 0):
        for message_id in range(min_id + 1, min_id + limit + 1):
            yield FakeMessage(message_id, f"async message {message_id}")


@dataclass
class FakeNewUpdate:
    message: object
    pts: int


@dataclass
class FakeEditUpdate:
    message: object
    pts: int


@dataclass
class FakeDeleteUpdate:
    messages: list[int]
    pts: int


class UpdateClient:
    def __init__(self, updates):
        self.updates = updates

    def iter_updates(self, peer_id: str, *, limit: int, marker=None):
        return self.updates

    def iter_messages(self, peer_id: str, *, limit: int, offset_id: int = 0, min_id: int = 0):
        return []


class FloodClient:
    def iter_messages(self, peer_id: str, *, limit: int, offset_id: int = 0):
        raise FakeFloodWait()


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


def test_public_history_uses_mtproto_and_round_trips_rich_metadata(tmp_path: Path) -> None:
    src = source(access_modes=["READ_PUBLIC"], allowlisted_peer_ids=[], peer_id="-100123", title="Midnight Quantum")
    gw = gateway(tmp_path, src)
    facade = TelegramMTProtoFacade(src, client_factory=lambda: RichClient())

    result = gw.get_history("@midnightquantum", limit=10, mtproto=facade)

    assert result["status"] == "DONE"
    message = result["messages"][0]
    assert message["peer_channel_id"] == "-100123"
    assert message["username"] == "midnightquantum"
    assert message["title"] == "Midnight Quantum"
    assert message["sender"] == {"id": "7", "username": "operator", "name": "Ada Lovelace"}
    assert message["author"] == "Channel Admin"
    assert message["urls"] == ["https://example.test"]
    assert message["reply_to_message_id"] == 99
    assert message["thread_id"] == 12
    assert message["forwarded_from"]["from_name"] == "Upstream"
    assert message["media"]["caption"] == "media caption"
    assert message["permalink"] == "https://t.me/midnightquantum/101"


def test_public_history_supports_async_telethon_shaped_client(tmp_path: Path) -> None:
    src = source(access_modes=["READ_PUBLIC"], max_page_size=3)
    gw = gateway(tmp_path, src)
    client = AsyncClient()

    result = gw.get_history("@midnightquantum", limit=2, mtproto=TelegramMTProtoFacade(src, client_factory=lambda: client))

    assert result["status"] == "DONE"
    assert client.connected is True
    assert [message["message_id"] for message in result["messages"]] == [2, 1]


def test_private_access_requires_exact_allowlisted_peer_but_public_does_not(tmp_path: Path) -> None:
    public_src = source(access_modes=["READ_PUBLIC"], allowlisted_peer_ids=[])
    public_gw = gateway(tmp_path, public_src)

    public_result = public_gw.get_history("@midnightquantum", mtproto=TelegramMTProtoFacade(public_src, client_factory=lambda: RichClient()))
    assert public_result["status"] == "DONE"

    private_src = source(access_modes=["READ_ALLOWED_PRIVATE"], allowlisted_peer_ids=["12345"])
    private_gw = gateway(tmp_path, private_src)
    blocked = private_gw.resolve_source("@midnightquantum", mode=TelegramAccessMode.READ_ALLOWED_PRIVATE)
    assert blocked == {"status": "BLOCKED", "reason_code": "PEER_NOT_ALLOWLISTED"}


def test_private_access_accepts_stable_peer_allowlist_without_handle(tmp_path: Path) -> None:
    private_src = source(access_modes=["READ_ALLOWED_PRIVATE"], peer_id="-100123", allowlisted_peer_ids=["-100123"])
    private_gw = gateway(tmp_path, private_src)

    resolved = private_gw.resolve_source("midnight", mode=TelegramAccessMode.READ_ALLOWED_PRIVATE)

    assert resolved["status"] == "DONE"
    assert resolved["source"]["peer_id"] == "-100123"


def test_get_message_history_date_filters_and_search_filters(tmp_path: Path) -> None:
    gw = gateway(tmp_path)
    gw.read_public_seed(
        "midnight",
        [
            TelegramMessage(source_id="midnight", peer_id="@midnightquantum", message_id=1, text="старий alpha", sent_at="2026-09-25T00:00:00Z"),
            TelegramMessage(source_id="midnight", peer_id="@midnightquantum", message_id=2, text="new alpha", sent_at="2026-09-26T00:00:00Z"),
        ],
    )

    assert gw.get_message("@midnightquantum", 2)["message"]["text"] == "new alpha"
    assert [row["message_id"] for row in gw.store.get_history(source_id="midnight", peer_id="@midnightquantum", min_date="2026-09-26T00:00:00Z")] == [2]
    assert [row["message_id"] for row in gw.search("alpha", source_ref="@midnightquantum", min_date="2026-09-26T00:00:00Z")["messages"]] == [2]


def test_get_message_can_bounded_fetch_when_cache_misses(tmp_path: Path) -> None:
    src = source(access_modes=["READ_PUBLIC"])
    gw = gateway(tmp_path, src)

    result = gw.get_message("@midnightquantum", 101, mtproto=TelegramMTProtoFacade(src, client_factory=lambda: RichClient()))

    assert result["status"] == "DONE"
    assert result["message"]["message_id"] == 101


def test_sync_source_persists_cursor_and_reconciles_edits_without_duplicates(tmp_path: Path) -> None:
    src = source(access_modes=["READ_PUBLIC"])
    gw = gateway(tmp_path, src)
    first = type("EditableMessage", (), {"id": 5, "message": "before edit"})()
    second = type("EditableMessage", (), {"id": 5, "message": "after edit"})()

    assert gw.sync_source("@midnightquantum", mtproto=TelegramMTProtoFacade(src, client_factory=lambda: RichClient([first])))["cursor_message_id"] == 5
    restarted = TelegramGateway(allowlist=TelegramAllowlist({"midnight": src}), store=TelegramStore.open(tmp_path / "telegram.db"))
    assert restarted.sync_source("@midnightquantum", mtproto=TelegramMTProtoFacade(src, client_factory=lambda: RichClient([second])))["cursor_message_id"] == 5

    stored = restarted.get_message("@midnightquantum", 5)["message"]
    assert stored["text"] == "after edit"
    assert restarted.store.latest_message_id(source_id="midnight", peer_id="@midnightquantum") == 5


def test_sync_source_applies_update_edits_deletes_and_marker(tmp_path: Path) -> None:
    src = source(access_modes=["READ_PUBLIC"])
    gw = gateway(tmp_path, src)
    original = type("Msg", (), {"id": 10, "message": "original"})()
    edited = type("Msg", (), {"id": 10, "message": "edited"})()
    deleted = type("Msg", (), {"id": 11, "message": "delete me"})()
    gw.read_public_seed(
        "midnight",
        [
            TelegramMessage(source_id="midnight", peer_id="@midnightquantum", message_id=10, text="seed"),
            TelegramMessage(source_id="midnight", peer_id="@midnightquantum", message_id=11, text="delete me"),
        ],
    )

    result = gw.sync_source(
        "@midnightquantum",
        mtproto=TelegramMTProtoFacade(
            src,
            client_factory=lambda: UpdateClient([FakeNewUpdate(original, 1), FakeEditUpdate(edited, 2), FakeDeleteUpdate([11], 3)]),
        ),
    )

    assert result["status"] == "DONE"
    assert result["deleted_count"] == 1
    assert gw.get_message("@midnightquantum", 10)["message"]["text"] == "edited"
    assert gw.store.get_message(source_id="midnight", peer_id="@midnightquantum", message_id=11)["deleted_at"] is not None
    assert gw.store.get_sync_cursor(source_id="midnight", peer_id="@midnightquantum")["state"]["update_marker"] == 3


def test_flood_wait_is_bounded_audited_and_does_not_advance_cursor(tmp_path: Path) -> None:
    src = source(access_modes=["READ_PUBLIC"])
    gw = gateway(tmp_path, src)

    result = gw.sync_source("@midnightquantum", mtproto=TelegramMTProtoFacade(src, client_factory=lambda: FloodClient()))

    assert result["status"] == "FLOOD_WAIT"
    assert result["retry_after_seconds"] == 13
    assert gw.store.get_sync_cursor(source_id="midnight", peer_id="@midnightquantum") is None
    assert "message" not in str(gw.audit_log.events[-1]).lower()


def test_watch_and_list_allowed_sources_are_audited(tmp_path: Path) -> None:
    gw = gateway(tmp_path)

    watch = gw.watch_source("@midnightquantum", min_message_id=40)
    listed = gw.list_allowed_sources()

    assert watch["watch"]["min_message_id"] == 40
    assert listed["sources"][0]["handle"] == "@midnightquantum"
    assert [event["action"] for event in gw.audit_log.events[-2:]] == ["WATCH_SOURCE", "LIST_ALLOWED_SOURCES"]


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
