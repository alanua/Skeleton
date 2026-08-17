from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from adapters.gmail_mail_provider import build_registered_gmail_provider
from core.mail_operations import MailOperationError
from core.mail_provider import MailProviderAccount, MailProviderCursor


MAIL_GMAIL_READONLY_CANARY_TASK_ID = "mail_gmail_readonly_canary_v1"
_ALLOWED_ACCOUNTS = frozenset({"acct:gmail-primary", "acct:gmail-secondary"})
_FIXED_QUERY = "newer_than:30d"


class GmailReadonlyCanaryError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


ProviderFactory = Callable[..., Any]


def run_gmail_readonly_canary(
    *,
    account_alias: str,
    authority_environment: Mapping[str, str],
    provider_factory: ProviderFactory = build_registered_gmail_provider,
) -> dict[str, object]:
    """Probe one registered Gmail account without exposing any message or secret data."""

    if account_alias not in _ALLOWED_ACCOUNTS:
        raise GmailReadonlyCanaryError("GMAIL_ACCOUNT_ALIAS_NOT_ALLOWED")
    try:
        provider = provider_factory(
            account_ref=account_alias,
            authority_environment=authority_environment,
        )
        batch = provider.poll(
            MailProviderAccount(
                account_ref=account_alias,
                provider="gmail",
                poll_interval_seconds=300,
                max_messages_per_poll=1,
                query=_FIXED_QUERY,
            ),
            MailProviderCursor(account_ref=account_alias),
            max_messages=1,
        )
    except MailOperationError as exc:
        raise GmailReadonlyCanaryError(_stable_reason(exc.reason_code)) from None
    except Exception:
        raise GmailReadonlyCanaryError("GMAIL_READONLY_PROVIDER_FAILURE") from None

    count = len(batch.messages)
    if count not in {0, 1}:
        raise GmailReadonlyCanaryError("GMAIL_READONLY_BOUNDS_VIOLATION")
    return {
        "maintenance_task_id": MAIL_GMAIL_READONLY_CANARY_TASK_ID,
        "account_alias": account_alias,
        "credential_binding_status": "USED",
        "oauth_refresh_status": "PASS",
        "gmail_readonly_status": "PASS",
        "probed_message_count": count,
        "mutation_attempted": False,
        "content_exposed": False,
        "stable_reason": "OK",
        "success_criteria": "met",
    }


def blocked_gmail_readonly_receipt(*, account_alias: str, reason_code: str) -> dict[str, object]:
    """Public-safe blocked receipt. Never forward exception text or private payloads."""

    safe_alias = account_alias if account_alias in _ALLOWED_ACCOUNTS else "UNREGISTERED"
    return {
        "maintenance_task_id": MAIL_GMAIL_READONLY_CANARY_TASK_ID,
        "account_alias": safe_alias,
        "credential_binding_status": "BLOCKED",
        "oauth_refresh_status": "BLOCKED",
        "gmail_readonly_status": "BLOCKED",
        "probed_message_count": 0,
        "mutation_attempted": False,
        "content_exposed": False,
        "stable_reason": _stable_reason(reason_code),
        "success_criteria": "not_met",
    }


def allowed_gmail_canary_accounts() -> tuple[str, ...]:
    return tuple(sorted(_ALLOWED_ACCOUNTS))


def _stable_reason(reason_code: str) -> str:
    known = {
        "GMAIL_OAUTH_REVOKED",
        "GMAIL_OAUTH_SCOPE_INVALID",
        "GMAIL_CREDENTIAL_UNAVAILABLE",
        "GMAIL_ACCOUNT_ALIAS_NOT_ALLOWED",
        "GMAIL_READONLY_BOUNDS_VIOLATION",
        "GMAIL_READONLY_PROVIDER_FAILURE",
        "GMAIL_HTTP_ERROR",
        "GMAIL_TOKEN_REFRESH_FAILED",
    }
    return reason_code if reason_code in known else "GMAIL_READONLY_PROVIDER_FAILURE"
