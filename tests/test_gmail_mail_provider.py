from adapters.gmail_mail_provider import GmailMailProvider
from core.mail_provider import MailProviderAccount


def _account(secret=True):
    return MailProviderAccount.from_mapping(
        {
            "schema": "skeleton.mail_provider.account.v1",
            "account_ref": "acct.gmail.primary",
            "provider": "gmail",
            "secret_reference": (
                {"ref": "secretref:gmail-primary", "kind": "oauth_refresh_token"} if secret else None
            ),
        }
    )


def test_gmail_provider_missing_secret_is_auth_required() -> None:
    result = GmailMailProvider().poll(_account(secret=False), cursor=None, now=1)

    assert result.status == "AUTH_REQUIRED"
    assert result.public_receipt()["private_payloads_included"] is False


def test_gmail_provider_normalizes_transport_messages() -> None:
    provider = GmailMailProvider(
        transport=lambda _account, _cursor, _now: {
            "status": "OK",
            "next_cursor": "h2",
            "messages": [
                {
                    "id": "m1",
                    "thread_id": "t1",
                    "history_id": "h1",
                    "received_at": 10,
                    "subject": "Important",
                    "body_preview": "Deadline 2026-09-01",
                    "sender": "person@example.invalid",
                    "labels": ["IMPORTANT"],
                }
            ],
        }
    )

    result = provider.poll(_account(), cursor=None, now=11)

    assert result.status == "OK"
    assert result.next_cursor == "h2"
    assert result.messages[0].provider == "gmail"
    assert result.messages[0].to_envelope("acct.gmail.primary")["importance_hint"] == "important"
