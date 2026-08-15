from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from urllib import parse

from adapters.gmail_mail_provider import GmailMailProvider
from adapters.gmail_oauth_client import (
    GMAIL_READONLY_SCOPE,
    GmailCredentialBundle,
    GmailCredentialStore,
    GmailOAuthError,
    GmailOAuthReadOnlyClient,
    build_authorization_url,
    exchange_authorization_code,
)
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


def _account() -> MailProviderAccount:
    return MailProviderAccount.from_mapping(
        {
            "schema": "skeleton.mail_provider_account.v1",
            "account_ref": "acct:gmail-primary",
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


def test_gmail_oauth_client_refreshes_and_reads_metadata_offline(tmp_path: Path) -> None:
    store = GmailCredentialStore(tmp_path / "credentials")
    store.save(
        GmailCredentialBundle(
            account_ref="acct:gmail-primary",
            client_id="synthetic-client-id",
            client_secret="synthetic-client-secret",
            refresh_token="synthetic-refresh-token",
            scopes=(GMAIL_READONLY_SCOPE,),
            expires_at=0,
        )
    )
    calls: list[tuple[str, str]] = []

    def fake_http(url: str, *, method: str, headers=None, data=None):
        calls.append((method, url))
        if url == "https://oauth2.googleapis.com/token":
            parsed = parse.parse_qs(data.decode("utf-8"))
            assert parsed["grant_type"] == ["refresh_token"]
            return (
                200,
                {},
                json.dumps(
                    {
                        "access_token": "synthetic-access-token",
                        "expires_in": 3600,
                        "scope": GMAIL_READONLY_SCOPE,
                    }
                ).encode("utf-8"),
            )
        assert headers == {"Authorization": "Bearer synthetic-access-token"}
        if url.startswith("https://gmail.googleapis.com/gmail/v1/users/me/messages?"):
            return 200, {}, b'{"messages":[{"id":"msg-1"}]}'
        if "/messages/msg-1?" in url:
            return (
                200,
                {},
                b'{"id":"msg-1","threadId":"thread-1","internalDate":"1786400000000",'
                b'"payload":{"headers":[{"name":"Subject","value":"Invoice"}]}}',
            )
        raise AssertionError(url)

    client = GmailOAuthReadOnlyClient(
        account_ref="acct:gmail-primary",
        credential_store=store,
        http_request=fake_http,
        clock=lambda: 1786400000,
    )

    messages = client.list_messages(query="label:important", max_results=5)

    assert messages[0]["id"] == "msg-1"
    assert [method for method, _ in calls] == ["POST", "GET", "GET"]
    refreshed = json.loads(store.path_for("acct:gmail-primary").read_text(encoding="utf-8"))
    assert refreshed["expires_at"] == 1786403600


def test_gmail_credential_store_fails_closed_for_missing_or_public_files(tmp_path: Path) -> None:
    store = GmailCredentialStore(tmp_path / "credentials")

    try:
        store.load("acct:gmail-primary")
    except GmailOAuthError as exc:
        assert exc.reason_code == "GMAIL_CREDENTIAL_MISSING"
    else:  # pragma: no cover
        raise AssertionError("expected missing credential failure")

    store.save(
        GmailCredentialBundle(
            account_ref="acct:gmail-primary",
            client_id="synthetic-client-id",
            client_secret="synthetic-client-secret",
            refresh_token="synthetic-refresh-token",
            scopes=(GMAIL_READONLY_SCOPE,),
        )
    )
    os.chmod(store.path_for("acct:gmail-primary"), 0o644)

    try:
        store.load("acct:gmail-primary")
    except GmailOAuthError as exc:
        assert exc.reason_code == "GMAIL_CREDENTIAL_PERMISSIONS_INVALID"
    else:  # pragma: no cover
        raise AssertionError("expected permissions failure")


def test_oauth_onboarding_helpers_store_private_bundle_without_account_metadata(
    tmp_path: Path,
) -> None:
    url = build_authorization_url(
        client_id="synthetic-client-id",
        redirect_uri="urn:ietf:wg:oauth:2.0:oob",
        state="synthetic-state",
    )
    params = parse.parse_qs(parse.urlparse(url).query)
    assert params["scope"] == [GMAIL_READONLY_SCOPE]
    assert params["access_type"] == ["offline"]

    def fake_http(url: str, *, method: str, headers=None, data=None):
        assert method == "POST"
        return (
            200,
            {},
            json.dumps(
                {
                    "refresh_token": "synthetic-refresh-token",
                    "access_token": "synthetic-access-token",
                    "expires_in": 3600,
                    "scope": GMAIL_READONLY_SCOPE,
                }
            ).encode("utf-8"),
        )

    store = GmailCredentialStore(tmp_path / "credentials")
    path = exchange_authorization_code(
        account_ref="acct:gmail-secondary",
        client_id="synthetic-client-id",
        client_secret="synthetic-client-secret",
        code="synthetic-code",
        redirect_uri="urn:ietf:wg:oauth:2.0:oob",
        credential_store=store,
        http_request=fake_http,
        clock=lambda: 1786400000,
    )

    assert path == store.path_for("acct:gmail-secondary")
    assert path.name != "acct:gmail-secondary.json"
    assert (path.stat().st_mode & 0o077) == 0
