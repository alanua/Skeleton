from __future__ import annotations

import sys
import types

import pytest

from core.telegram_gateway import TelegramGateway
from core.telegram_memory_bridge import TelegramMemoryProposalBridge
from core.telegram_mtproto import TelegramMTProtoFacade
from core.telegram_permissions import TelegramAllowlist, TelegramGatewayError, TelegramSource
from core.telegram_store import TelegramStore


def make_source(**overrides: object) -> TelegramSource:
    values = {
        "source_id": "midnight",
        "handle": "@midnightquantum",
        "access_modes": ["READ_ALLOWED_PRIVATE", "WRITE_BOT"],
        "secret_refs": ["telegram_api_id", "telegram_api_hash", "telegram_bot_token", "telegram_mtproto_string_session"],
        "allowlisted_peer_ids": ["@midnightquantum"],
        "operator_secret_route": "home_edge_devices_secrets_tab_to_bitwarden",
    }
    values.update(overrides)
    return TelegramSource.from_mapping(values)


def test_source_rejects_env_or_plaintext_secret_route() -> None:
    with pytest.raises(TelegramGatewayError) as excinfo:
        make_source(operator_secret_route="environment")

    assert excinfo.value.reason_code == "SECRET_ROUTE_NOT_ALLOWED"


def test_source_rejects_unknown_secret_refs() -> None:
    with pytest.raises(TelegramGatewayError) as excinfo:
        make_source(secret_refs=["telegram_mtproto_string_session", "plaintext_session"])

    assert excinfo.value.reason_code == "SECRET_REF_NOT_ALLOWED"


def test_private_read_requires_peer_allowlist(tmp_path) -> None:
    source = make_source()
    gw = TelegramGateway(allowlist=TelegramAllowlist({"midnight": source}), store=TelegramStore.open(tmp_path / "telegram.db"))

    result = gw.read_allowed_private("midnight", "not-allowed")

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "PEER_NOT_ALLOWLISTED"


def test_mtproto_missing_dependency_or_secret_resolution_fails_closed() -> None:
    facade = TelegramMTProtoFacade(make_source(), secret_resolver=None)

    with pytest.raises(TelegramGatewayError) as excinfo:
        facade.read_messages("@midnightquantum")

    assert excinfo.value.reason_code == "AUTH_REQUIRED"


def test_gateway_default_live_facade_uses_injected_secret_resolver(tmp_path, monkeypatch) -> None:
    source = make_source(access_modes=["READ_PUBLIC"], allowlisted_peer_ids=[])
    resolved_refs = []

    def resolver(ref: str) -> str | None:
        resolved_refs.append(ref)
        return {
            "telegram_api_id": "1",
            "telegram_api_hash": "hash-ref-material",
            "telegram_mtproto_string_session": "session-ref-material",
        }.get(ref)

    class FakeStringSession:
        def __init__(self, value):
            self.value = value

    class FakeTelegramClient:
        def __init__(self, session, api_id, api_hash):
            assert session.value == "session-ref-material"
            assert api_id == 1
            assert api_hash == "hash-ref-material"

        def is_connected(self):
            return True

        def is_user_authorized(self):
            return True

        def iter_messages(self, peer_id: str, *, limit: int, offset_id: int = 0, min_id: int = 0):
            return [types.SimpleNamespace(id=1, message="resolver wired")]

    telethon = types.ModuleType("telethon")
    telethon.TelegramClient = FakeTelegramClient
    sessions = types.ModuleType("telethon.sessions")
    sessions.StringSession = FakeStringSession
    monkeypatch.setitem(sys.modules, "telethon", telethon)
    monkeypatch.setitem(sys.modules, "telethon.sessions", sessions)

    gw = TelegramGateway(
        allowlist=TelegramAllowlist({"midnight": source}),
        store=TelegramStore.open(tmp_path / "telegram.db"),
        secret_resolver=resolver,
    )

    result = gw.get_history("@midnightquantum")

    assert result["status"] == "DONE"
    assert result["messages"][0]["text"] == "resolver wired"
    assert resolved_refs == ["telegram_api_id", "telegram_api_hash", "telegram_mtproto_string_session"]


def test_mtproto_uses_home_edge_authorization_provider_only_for_missing_session() -> None:
    source = make_source()
    calls = []

    def resolver(ref: str) -> str | None:
        return {"telegram_api_id": "1", "telegram_api_hash": "hash"}.get(ref)

    def provider(src, refs):
        calls.append((src.source_id, refs))
        return {"telegram_mtproto_string_session": "session"}

    facade = TelegramMTProtoFacade(source, secret_resolver=resolver, authorization_provider=provider)

    assert facade._resolve("telegram_mtproto_string_session") == "session"
    assert calls == [("midnight", source.secret_refs)]


def test_mtproto_existing_session_does_not_reprompt_provider() -> None:
    source = make_source()

    def resolver(ref: str) -> str | None:
        return "already-present" if ref == "telegram_mtproto_string_session" else None

    def provider(_src, _refs):
        raise AssertionError("provider should not be called")

    facade = TelegramMTProtoFacade(source, secret_resolver=resolver, authorization_provider=provider)

    assert facade._resolve("telegram_mtproto_string_session") == "already-present"


def test_mtproto_persists_new_authorized_session_only_via_provider_callback() -> None:
    source = make_source()
    persisted = []

    def resolver(ref: str) -> str | None:
        return {"telegram_api_id": "1", "telegram_api_hash": "hash"}.get(ref)

    def provider(_src, _refs):
        return {"telegram_mtproto_string_session": "new-session-material"}

    def persist(src, session):
        persisted.append((src.source_id, session))

    facade = TelegramMTProtoFacade(source, secret_resolver=resolver, authorization_provider=provider, session_persist=persist)

    assert facade._resolve("telegram_mtproto_string_session") == "new-session-material"
    assert persisted == [("midnight", "new-session-material")]


def test_mtproto_unauthorized_session_fails_closed() -> None:
    class UnauthorizedClient:
        def iter_messages(self, peer_id: str, *, limit: int, offset_id: int = 0, min_id: int = 0):
            return []

        def is_user_authorized(self):
            return False

    facade = TelegramMTProtoFacade(make_source(), client_factory=lambda: UnauthorizedClient())

    with pytest.raises(TelegramGatewayError) as excinfo:
        facade.read_messages("@midnightquantum")

    assert excinfo.value.reason_code == "AUTH_REQUIRED"


@pytest.mark.parametrize(
    "operation",
    ["send_message", "edit_message", "delete_messages", "react", "forward_messages", "join_channel", "leave_channel", "invite_to_channel", "pin_message"],
)
def test_user_account_write_operations_are_unavailable(operation: str) -> None:
    facade = TelegramMTProtoFacade(make_source(), client_factory=lambda: object())

    with pytest.raises(TelegramGatewayError) as excinfo:
        getattr(facade, operation)("anything")

    assert excinfo.value.reason_code == "OPERATION_NOT_AVAILABLE"


def test_raw_mtproto_client_is_not_exposed() -> None:
    facade = TelegramMTProtoFacade(make_source(), client_factory=lambda: object())

    with pytest.raises(AttributeError):
        getattr(facade, "client")


def test_memory_bridge_rejects_raw_bulk_and_direct_canonical_write() -> None:
    bridge = TelegramMemoryProposalBridge()

    with pytest.raises(TelegramGatewayError) as excinfo:
        bridge.propose(
            fact="one\ntwo\nthree\nfour\nfive",
            recommendation=None,
            provenance={
                "kind": "telegram_message_extract",
                "source_id": "midnight",
                "peer_id_hash": "hash",
                "message_ref_hash": "ref",
                "message_id": 1,
                "source_evidence_hash": "evidence",
                "provenance_hash": "provenance",
                "confidence": 0.7,
            },
        )
    assert excinfo.value.reason_code == "RAW_BULK_INGEST_FORBIDDEN"

    with pytest.raises(TelegramGatewayError) as excinfo:
        bridge.direct_canonical_write({"fact": "x"})
    assert excinfo.value.reason_code == "DIRECT_CANONICAL_WRITE_FORBIDDEN"


def test_memory_proposal_contains_only_bounded_provenance(tmp_path) -> None:
    source = make_source()
    gw = TelegramGateway(allowlist=TelegramAllowlist({"midnight": source}), store=TelegramStore.open(tmp_path / "telegram.db"))

    proposal = gw.propose_memory_fact(
        {
            "source_id": "midnight",
            "peer_id": "@midnightquantum",
            "message_id": 1,
            "sent_at": "2026-09-26T12:00:00+00:00",
            "permalink": "https://t.me/midnightquantum/1",
            "text": "raw text must not be copied",
        },
        fact="Operator prefers bounded Telegram summaries.",
        recommendation="Use summaries before proposals.",
    )

    assert proposal["mode"] == "proposal_only"
    assert proposal["direct_canonical_write"] is False
    assert "raw text" not in str(proposal)
    assert proposal["provenance"]["peer_id_hash"]
    assert proposal["provenance"]["message_ref_hash"]
    assert proposal["provenance"]["sent_at"] == "2026-09-26T12:00:00+00:00"
    assert proposal["provenance"]["permalink"] == "https://t.me/midnightquantum/1"
    assert proposal["provenance"]["confidence"] == 0.7


def test_audit_redacts_secret_markers(tmp_path) -> None:
    source = make_source()
    gw = TelegramGateway(allowlist=TelegramAllowlist({"midnight": source}), store=TelegramStore.open(tmp_path / "telegram.db"))

    event = gw.audit_log.record(action="READ_ALLOWED_PRIVATE", source_id="midnight", status="BLOCKED", details={"session": "secret"})

    assert event["details"]["session"] == "REDACTED"
