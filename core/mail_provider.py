from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Final, Protocol


MAIL_PROVIDER_ACCOUNT_SCHEMA: Final = "skeleton.mail_provider.account.v1"

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PROVIDERS = frozenset({"gmail"})
_SECRET_KINDS = frozenset({"oauth_refresh_token", "oauth_access_token", "service_account"})


class MailProviderError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SecretReference:
    """Opaque pointer to secret material held outside public receipts."""

    ref: str
    kind: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SecretReference":
        if not isinstance(value, Mapping):
            raise MailProviderError("INVALID_SECRET_REFERENCE", "secret_reference must be an object")
        ref = _bounded_ref(value.get("ref"), "secret_reference.ref", 256)
        kind = _enum(value.get("kind"), _SECRET_KINDS, "secret_reference.kind")
        return cls(ref=ref, kind=kind)

    def to_mapping(self) -> dict[str, str]:
        return {"ref": self.ref, "kind": self.kind}


@dataclass(frozen=True)
class MailProviderAccount:
    account_ref: str
    provider: str
    secret_reference: SecretReference | None
    poll_label: str = "INBOX"
    query: str = "in:anywhere"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MailProviderAccount":
        if not isinstance(value, Mapping):
            raise MailProviderError("INVALID_ACCOUNT", "account must be an object")
        if value.get("schema") != MAIL_PROVIDER_ACCOUNT_SCHEMA:
            raise MailProviderError("INVALID_ACCOUNT_SCHEMA", "invalid account schema")
        secret = value.get("secret_reference")
        return cls(
            account_ref=_safe_token(value.get("account_ref"), "account_ref"),
            provider=_enum(value.get("provider"), _PROVIDERS, "provider"),
            secret_reference=(
                None if secret is None else SecretReference.from_mapping(secret)
            ),
            poll_label=_safe_token(value.get("poll_label", "INBOX"), "poll_label"),
            query=_bounded_ref(value.get("query", "in:anywhere"), "query", 256),
        )

    def to_public_mapping(self) -> dict[str, Any]:
        return {
            "schema": MAIL_PROVIDER_ACCOUNT_SCHEMA,
            "account_ref": self.account_ref,
            "provider": self.provider,
            "poll_label": self.poll_label,
            "auth_configured": self.secret_reference is not None,
        }


@dataclass(frozen=True)
class MailProviderMessage:
    provider: str
    provider_message_id: str
    provider_thread_id: str | None
    history_id: str | None
    received_at: int
    subject: str
    body_preview: str
    sender: str | None = None
    labels: tuple[str, ...] = ()
    attachment_refs: tuple[str, ...] = ()
    headers: Mapping[str, str] | None = None

    def stable_provider_ref(self, account_ref: str) -> str:
        digest = _stable_hash(
            {
                "account_ref": account_ref,
                "provider": self.provider,
                "provider_message_id": self.provider_message_id,
            }
        )
        return f"mailmsg:{digest[:32]}"

    def thread_ref(self, account_ref: str) -> str | None:
        if not self.provider_thread_id:
            return None
        digest = _stable_hash(
            {
                "account_ref": account_ref,
                "provider": self.provider,
                "provider_thread_id": self.provider_thread_id,
            }
        )
        return f"thread:{digest[:32]}"

    def sender_ref(self, account_ref: str) -> str | None:
        if not self.sender:
            return None
        digest = _stable_hash({"account_ref": account_ref, "sender": self.sender.lower()})
        return f"sender:{digest[:32]}"

    def to_envelope(self, account_ref: str) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "provider_message_ref": self.stable_provider_ref(account_ref),
            "received_at": self.received_at,
            "subject_hint": self.subject or "No subject",
            "body_preview": self.body_preview or "No preview",
            "importance_hint": _importance_hint(self.labels, self.subject, self.body_preview),
            "deadline_hint": _deadline_hint(self.subject, self.body_preview),
            "sender_ref": self.sender_ref(account_ref),
            "thread_ref": self.thread_ref(account_ref),
        }


class MailProvider(Protocol):
    provider_name: str

    def poll(
        self, account: MailProviderAccount, *, cursor: str | None, now: int
    ) -> "MailProviderPollResult":
        ...


@dataclass(frozen=True)
class MailProviderPollResult:
    status: str
    messages: Sequence[MailProviderMessage]
    next_cursor: str | None
    reason: str = "OK"

    def public_receipt(self) -> dict[str, Any]:
        return {
            "schema": "skeleton.mail_provider.poll_receipt.v1",
            "status": self.status,
            "reason": self.reason,
            "message_count": len(self.messages),
            "cursor_advanced": self.next_cursor is not None,
            "public_safe": True,
            "private_payloads_included": False,
            "external_side_effects_executed": False,
        }


def auth_required_result(reason: str = "AUTH_REQUIRED") -> MailProviderPollResult:
    return MailProviderPollResult(
        status="AUTH_REQUIRED",
        reason=reason,
        messages=(),
        next_cursor=None,
    )


def _importance_hint(labels: Sequence[str], subject: str, body_preview: str) -> str | None:
    lowered_labels = {item.lower() for item in labels}
    lowered_text = f"{subject} {body_preview}".lower()
    if "important" in lowered_labels or any(
        token in lowered_text for token in ("urgent", "important", "deadline", "action required")
    ):
        return "important"
    return None


def _deadline_hint(subject: str, body_preview: str) -> str | None:
    text = f"{subject} {body_preview}"
    match = re.search(r"\b20\d{2}-\d{2}-\d{2}\b", text)
    return None if match is None else match.group(0)


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_token(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SAFE_TOKEN_RE.fullmatch(value) is None:
        raise MailProviderError("INVALID_TOKEN", f"{field} must be a safe token")
    return value


def _bounded_ref(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise MailProviderError("INVALID_TEXT", f"{field} must be text")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > limit:
        raise MailProviderError("INVALID_TEXT", f"{field} must be non-empty and bounded")
    if any(marker in normalized.lower() for marker in ("bearer ", "refresh_token=", "access_token=")):
        raise MailProviderError("SECRET_VALUE_NOT_ALLOWED", f"{field} must be an opaque reference")
    return normalized


def _enum(value: Any, allowed: frozenset[str], field: str) -> str:
    if value not in allowed:
        raise MailProviderError("INVALID_ENUM", f"{field} is not supported")
    return value
