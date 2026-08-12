from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import base64
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol


MAIL_PROVIDER_ACCOUNT_SCHEMA = "skeleton.mail_provider.account.v1"
AUTH_REQUIRED = "AUTH_REQUIRED"
_SAFE_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_SAFE_PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,31}$")


class MailProviderError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SecretReference:
    kind: str
    name: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SecretReference":
        if not isinstance(value, Mapping):
            raise MailProviderError("INVALID_SECRET_REFERENCE", "secret reference must be an object")
        kind = value.get("kind")
        name = value.get("name")
        if kind not in {"env", "file"}:
            raise MailProviderError("INVALID_SECRET_REFERENCE", "secret reference kind is not supported")
        if not isinstance(name, str) or not name.strip() or len(name) > 512:
            raise MailProviderError("INVALID_SECRET_REFERENCE", "secret reference name is invalid")
        return cls(kind=kind, name=name.strip())

    def resolve(self) -> str | None:
        if self.kind == "env":
            value = os.environ.get(self.name)
            return value if value else None
        if self.kind == "file":
            path = Path(self.name).expanduser()
            try:
                value = path.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            return value if value else None
        return None

    def to_public_mapping(self) -> dict[str, str]:
        return {"kind": self.kind, "name_hash": stable_hash(self.name)[:16]}


@dataclass(frozen=True)
class MailProviderAccount:
    provider: str
    alias: str
    credential_ref: SecretReference | None
    poll_interval_seconds: int = 300
    cleanup_enabled: bool = False
    label_after_handoff: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MailProviderAccount":
        if not isinstance(value, Mapping):
            raise MailProviderError("INVALID_ACCOUNT", "account must be an object")
        if value.get("schema") != MAIL_PROVIDER_ACCOUNT_SCHEMA:
            raise MailProviderError("INVALID_ACCOUNT_SCHEMA", "account schema is invalid")
        provider = _safe_provider(value.get("provider"))
        alias = _safe_alias(value.get("alias"))
        raw_secret = value.get("credential_ref")
        secret = SecretReference.from_mapping(raw_secret) if raw_secret is not None else None
        interval = _positive_int(value.get("poll_interval_seconds", 300), "poll_interval_seconds")
        if interval < 60 or interval > 3600:
            raise MailProviderError("INVALID_POLL_INTERVAL", "poll interval must be between 60 and 3600 seconds")
        cleanup_enabled = value.get("cleanup_enabled", False)
        if not isinstance(cleanup_enabled, bool):
            raise MailProviderError("INVALID_CLEANUP_FLAG", "cleanup flag must be boolean")
        label = value.get("label_after_handoff")
        if label is not None:
            label = _safe_alias(label)
        return cls(
            provider=provider,
            alias=alias,
            credential_ref=secret,
            poll_interval_seconds=interval,
            cleanup_enabled=cleanup_enabled,
            label_after_handoff=label,
        )

    def require_secret(self) -> str:
        secret = self.credential_ref.resolve() if self.credential_ref is not None else None
        if not secret:
            raise MailProviderError(AUTH_REQUIRED, "mail provider credentials are required")
        return secret

    def to_public_mapping(self) -> dict[str, Any]:
        return {
            "schema": MAIL_PROVIDER_ACCOUNT_SCHEMA,
            "provider": self.provider,
            "alias": self.alias,
            "credential_ref": (
                self.credential_ref.to_public_mapping() if self.credential_ref is not None else None
            ),
            "poll_interval_seconds": self.poll_interval_seconds,
            "cleanup_enabled": self.cleanup_enabled,
            "label_after_handoff": self.label_after_handoff,
        }


@dataclass(frozen=True)
class MailChange:
    provider_message_ref: str
    provider_thread_ref: str | None
    changed_at: int
    history_ref: str | None = None


@dataclass(frozen=True)
class MailAttachmentRef:
    attachment_ref: str
    mime_type: str | None = None
    size: int | None = None


@dataclass(frozen=True)
class ProviderMailMessage:
    provider: str
    provider_message_ref: str
    provider_thread_ref: str | None
    received_at: int
    subject: str
    body_preview: str
    sender: str | None = None
    labels: tuple[str, ...] = ()
    attachments: tuple[MailAttachmentRef, ...] = ()
    headers: Mapping[str, str] | None = None

    def envelope_for_operations(self) -> dict[str, Any]:
        sender_ref = stable_hash(self.sender or "unknown")[:24]
        thread_ref = stable_hash(self.provider_thread_ref or self.provider_message_ref)[:24]
        return {
            "provider": self.provider,
            "provider_message_ref": stable_hash(self.provider_message_ref),
            "thread_ref": f"thread:{thread_ref}",
            "sender_ref": f"sender:{sender_ref}",
            "received_at": self.received_at,
            "subject_hint": _hint(self.subject, default="Private mail subject"),
            "body_preview": _hint(self.body_preview, default="Private mail preview"),
            "importance_hint": "high" if self.is_flagged_important() else None,
            "deadline_hint": _deadline_hint(self.subject, self.body_preview),
        }

    def content_fingerprint(self) -> str:
        return stable_hash(
            {
                "provider": self.provider,
                "message": self.provider_message_ref,
                "thread": self.provider_thread_ref,
                "received": self.received_at,
                "subject": self.subject,
                "preview": self.body_preview,
                "attachments": [item.attachment_ref for item in self.attachments],
            }
        )

    def is_flagged_important(self) -> bool:
        return any(label.upper() in {"IMPORTANT", "CATEGORY_PRIMARY", "STARRED"} for label in self.labels)


@dataclass(frozen=True)
class ProviderScanResult:
    cursor: str
    changes: tuple[MailChange, ...]


@dataclass(frozen=True)
class MailCleanupAction:
    provider_message_ref: str
    action: str
    label: str | None = None


class MailProvider(Protocol):
    provider_name: str

    def scan(self, account: MailProviderAccount, *, cursor: str | None, limit: int) -> ProviderScanResult:
        ...

    def fetch_message(
        self, account: MailProviderAccount, *, provider_message_ref: str
    ) -> ProviderMailMessage:
        ...

    def apply_cleanup(
        self, account: MailProviderAccount, *, actions: Sequence[MailCleanupAction]
    ) -> Mapping[str, Any]:
        ...

    def send_message(self, *_args: Any, **_kwargs: Any) -> Mapping[str, Any]:
        ...


def provider_alias(account: MailProviderAccount) -> str:
    return f"{account.provider}:{account.alias}"


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def decode_base64url_text(value: str) -> str:
    padded = value + ("=" * (-len(value) % 4))
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _safe_provider(value: Any) -> str:
    if not isinstance(value, str) or _SAFE_PROVIDER_RE.fullmatch(value) is None:
        raise MailProviderError("INVALID_PROVIDER", "provider must be a safe token")
    return value


def _safe_alias(value: Any) -> str:
    if not isinstance(value, str) or _SAFE_ALIAS_RE.fullmatch(value) is None:
        raise MailProviderError("INVALID_ALIAS", "alias must be a safe token")
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MailProviderError("INVALID_INTEGER", f"{field} must be a positive integer")
    return value


def _hint(value: str, *, default: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        return default
    return normalized[:512]


def _deadline_hint(*values: str) -> str | None:
    joined = " ".join(values)
    match = re.search(r"\b20\d{2}-\d{2}-\d{2}\b", joined)
    return match.group(0) if match else None
