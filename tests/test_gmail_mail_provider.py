from __future__ import annotations

import base64

from adapters.gmail_mail_provider import GmailMailProvider
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
