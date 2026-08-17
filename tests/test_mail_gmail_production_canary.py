from __future__ import annotations

import json

import pytest

from core.mail_gmail_production_canary import (
    GmailReadonlyCanaryError,
    blocked_gmail_readonly_receipt,
    run_gmail_readonly_canary,
)
from core.mail_operations import MailEnvelope, MailOperationError
from core.mail_provider import MailProviderBatch


class _Provider:
    def __init__(self, messages=()):
        self.messages = tuple(messages)

    def poll(self, account, cursor, *, max_messages):
        assert account.query == "newer_than:30d"
        assert account.max_messages_per_poll == 1
        assert max_messages == 1
        assert cursor.account_ref == account.account_ref
        return MailProviderBatch(account.account_ref, "gmail", self.messages[:1], None)


def _message():
    return MailEnvelope.from_mapping(
        {
            "provider": "gmail",
            "provider_message_ref": "secret-message-id",
            "received_at": 1,
            "subject_hint": "SECRET SUBJECT",
            "body_preview": "SECRET BODY",
            "sender_ref": "sender:secret",
        }
    )


def test_success_receipt_contains_only_aggregate_status() -> None:
    def factory(**_kwargs):
        return _Provider((_message(),))

    receipt = run_gmail_readonly_canary(
        account_alias="acct:gmail-primary",
        authority_environment={},
        provider_factory=factory,
    )
    rendered = json.dumps(receipt, sort_keys=True)

    assert receipt["success_criteria"] == "met"
    assert receipt["probed_message_count"] == 1
    assert receipt["mutation_attempted"] is False
    assert receipt["content_exposed"] is False
    assert "SECRET SUBJECT" not in rendered
    assert "SECRET BODY" not in rendered
    assert "secret-message-id" not in rendered


def test_unknown_alias_rejected_before_provider_construction() -> None:
    called = False

    def factory(**_kwargs):
        nonlocal called
        called = True
        return _Provider()

    with pytest.raises(GmailReadonlyCanaryError) as error:
        run_gmail_readonly_canary(
            account_alias="acct:attacker",
            authority_environment={},
            provider_factory=factory,
        )

    assert error.value.reason_code == "GMAIL_ACCOUNT_ALIAS_NOT_ALLOWED"
    assert called is False


def test_revoked_oauth_is_stable_and_public_safe() -> None:
    def factory(**_kwargs):
        raise MailOperationError("GMAIL_OAUTH_REVOKED", "private provider error details")

    with pytest.raises(GmailReadonlyCanaryError) as error:
        run_gmail_readonly_canary(
            account_alias="acct:gmail-primary",
            authority_environment={},
            provider_factory=factory,
        )

    receipt = blocked_gmail_readonly_receipt(
        account_alias="acct:gmail-primary",
        reason_code=error.value.reason_code,
    )
    assert receipt["stable_reason"] == "GMAIL_OAUTH_REVOKED"
    assert "private provider error details" not in json.dumps(receipt)
    assert receipt["mutation_attempted"] is False
    assert receipt["content_exposed"] is False


def test_missing_credential_never_falls_back_to_static_provider() -> None:
    def factory(**_kwargs):
        raise MailOperationError("GMAIL_CREDENTIAL_UNAVAILABLE", "missing")

    with pytest.raises(GmailReadonlyCanaryError) as error:
        run_gmail_readonly_canary(
            account_alias="acct:gmail-secondary",
            authority_environment={},
            provider_factory=factory,
        )

    assert error.value.reason_code == "GMAIL_CREDENTIAL_UNAVAILABLE"
