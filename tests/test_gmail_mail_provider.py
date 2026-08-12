import json
from unittest import mock

import pytest

from adapters.gmail_mail_provider import GmailMailProvider
from core.mail_provider import MailProviderAccount, MailProviderError


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _account():
    return MailProviderAccount.from_mapping(
        {
            "schema": "skeleton.mail_provider.account.v1",
            "provider": "gmail",
            "alias": "primary",
            "credential_ref": {"kind": "env", "name": "TOKEN_ENV"},
            "poll_interval_seconds": 300,
            "cleanup_enabled": False,
        }
    )


def test_gmail_scan_uses_history_cursor_and_sanitized_refs(monkeypatch):
    monkeypatch.setenv("TOKEN_ENV", "token-value")
    requests = []

    def opener(request, timeout):
        requests.append(request)
        return Response(
            {
                "historyId": "43",
                "history": [{"id": "42", "messagesAdded": [{"message": {"id": "msg-1", "threadId": "t-1"}}]}],
            }
        )

    result = GmailMailProvider(opener=opener).scan(_account(), cursor="41", limit=10)

    assert result.cursor == "43"
    assert result.changes[0].provider_message_ref == "msg-1"
    assert requests[0].headers["Authorization"] == "Bearer token-value"


def test_gmail_fetch_metadata_does_not_request_raw_body(monkeypatch):
    monkeypatch.setenv("TOKEN_ENV", "token-value")
    requests = []

    def opener(request, timeout):
        requests.append(request)
        return Response(
            {
                "id": "msg-1",
                "threadId": "thread-1",
                "internalDate": "1786400000000",
                "labelIds": ["INBOX", "IMPORTANT"],
                "snippet": "Action required by 2026-09-01.",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Important deadline"},
                        {"name": "From", "value": "person@example.invalid"},
                    ]
                },
            }
        )

    message = GmailMailProvider(opener=opener).fetch_message(_account(), provider_message_ref="msg-1")

    assert "format=metadata" in requests[0].full_url
    assert message.subject == "Important deadline"
    assert message.is_flagged_important() is True


def test_gmail_send_disabled(monkeypatch):
    monkeypatch.setenv("TOKEN_ENV", "token-value")

    with pytest.raises(MailProviderError) as error:
        GmailMailProvider().send_message()
    assert error.value.reason_code == "SEND_DISABLED"


def test_gmail_missing_auth_fails_closed(monkeypatch):
    monkeypatch.delenv("TOKEN_ENV", raising=False)

    with pytest.raises(MailProviderError) as error:
        GmailMailProvider(opener=mock.Mock()).scan(_account(), cursor=None, limit=1)
    assert error.value.reason_code == "AUTH_REQUIRED"
