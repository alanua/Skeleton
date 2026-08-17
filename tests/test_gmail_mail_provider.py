from __future__ import annotations

import base64
import json

import pytest

from adapters import gmail_mail_provider
from adapters.gmail_mail_provider import GmailMailProvider, build_registered_gmail_provider
from adapters.gmail_oauth_client import GMAIL_READONLY_SCOPE, GOOGLE_TOKEN_URL, GMAIL_API_BASE, GmailOAuthBundle, GmailOAuthClient, GmailOAuthError
from core.mail_provider import MailProviderAccount, MailProviderCursor


class FakeGmailClient:
    def list_messages(self, *, query: str, max_results: int):
        assert query == "label:important"
        assert max_results == 2
        body = base64.urlsafe_b64encode(b"Invoice due 2026-09-01").decode("ascii").rstrip("=")
        return [
            {
                "id": "msg-1",
                "threadId": "thread-1",
                "internalDate": "1786400000000",
                "labelIds": ["IMPORTANT"],
                "snippet": "Invoice due 2026-09-01",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Invoice"},
                        {"name": "From", "value": "billing@example.invalid"},
                    ],
                    "body": {"data": body},
                },
            }
        ]


def _account(account_ref: str = "acct:gmail-primary") -> MailProviderAccount:
    return MailProviderAccount.from_mapping(
        {
            "schema": "skeleton.mail_provider_account.v1",
            "account_ref": account_ref,
            "provider": "gmail",
            "poll_interval_seconds": 300,
            "max_messages_per_poll": 2,
            "query": "label:important",
        }
    )


def test_gmail_adapter_normalizes_messages_without_live_mutation() -> None:
    batch = GmailMailProvider(FakeGmailClient()).poll(
        _account(),
        MailProviderCursor("acct:gmail-primary"),
        max_messages=2,
    )

    assert batch.provider == "gmail"
    assert batch.next_cursor_ref == "msg-1"
    assert len(batch.messages) == 1
    envelope = batch.messages[0]
    assert envelope.provider == "gmail"
    assert envelope.subject_hint == "Invoice"
    assert envelope.body_preview == "Invoice due 2026-09-01"
    assert envelope.importance_hint == "high"
    assert envelope.sender_ref.startswith("sender:")


def test_oauth_bundle_requires_readonly_scope() -> None:
    raw = json.dumps(
        {
            "client_id": "client",
            "client_secret": "secret",
            "refresh_token": "refresh",
            "scopes": ["profile"],
        }
    )
    with pytest.raises(GmailOAuthError, match="GMAIL_OAUTH_SCOPE_INSUFFICIENT"):
        GmailOAuthBundle.from_json(raw)


def test_oauth_client_refreshes_then_uses_get_only_for_gmail() -> None:
    calls: list[tuple[str, str, bytes | None, dict[str, str]]] = []

    def transport(method, url, body, headers):
        calls.append((method, url, body, dict(headers)))
        if url == GOOGLE_TOKEN_URL:
            assert method == "POST"
            assert body is not None and b"client_secret=secret" in body and b"refresh_token=refresh" in body
            return {"access_token": "access-secret"}
        assert method == "GET"
        assert headers["Authorization"] == "Bearer access-secret"
        if url.startswith(f"{GMAIL_API_BASE}/users/me/messages?"):
            return {"messages": [{"id": "msg-1"}]}
        return {"id": "msg-1", "internalDate": "1786400000000", "payload": {"headers": []}}

    bundle = GmailOAuthBundle.from_json(
        json.dumps(
            {
                "client_id": "client",
                "client_secret": "secret",
                "refresh_token": "refresh",
                "scopes": [GMAIL_READONLY_SCOPE],
            }
        )
    )
    rows = GmailOAuthClient(bundle, transport=transport).list_messages(query="label:important", max_results=2)

    assert len(rows) == 1
    assert [call[0] for call in calls] == ["POST", "GET", "GET"]
    assert all(call[1] == GOOGLE_TOKEN_URL or call[1].startswith(GMAIL_API_BASE) for call in calls)


def test_registered_provider_consumes_broker_material_without_secret_receipt(monkeypatch) -> None:
    sentinel = "gmail-refresh-SENTINEL"
    aliases: list[str] = []

    def fake_consume(*, service_id, alias, action_id, consumer, authority_environment):
        assert service_id == "mail-gmail"
        assert action_id == "use-gmail-readonly-oauth"
        assert authority_environment == {"AUTH": "1"}
        aliases.append(alias)
        consumer(
            json.dumps(
                {
                    "client_id": "client",
                    "client_secret": "client-secret",
                    "refresh_token": sentinel,
                    "scopes": [GMAIL_READONLY_SCOPE],
                }
            )
        )
        return {"result": {"status": "USED", "alias": alias}}

    monkeypatch.setattr(gmail_mail_provider, "consume_registered_material_credential", fake_consume)
    provider = build_registered_gmail_provider(
        account_ref="acct:gmail-primary",
        authority_environment={"AUTH": "1"},
        transport=lambda *_args: {"access_token": "unused"},
    )

    assert isinstance(provider, GmailMailProvider)
    assert aliases == ["acct:gmail-primary"]
    assert sentinel not in repr(provider)


def test_two_registered_accounts_are_independent(monkeypatch) -> None:
    aliases: list[str] = []

    def fake_consume(*, service_id, alias, action_id, consumer, authority_environment):
        aliases.append(alias)
        consumer(
            json.dumps(
                {
                    "client_id": f"client-{alias}",
                    "client_secret": f"secret-{alias}",
                    "refresh_token": f"refresh-{alias}",
                    "scopes": [GMAIL_READONLY_SCOPE],
                }
            )
        )
        return {"result": {"status": "USED"}}

    monkeypatch.setattr(gmail_mail_provider, "consume_registered_material_credential", fake_consume)
    first = build_registered_gmail_provider(account_ref="acct:gmail-primary", authority_environment={})
    second = build_registered_gmail_provider(account_ref="acct:gmail-secondary", authority_environment={})

    assert first is not second
    assert aliases == ["acct:gmail-primary", "acct:gmail-secondary"]
