"""OpenHands OAuth credentials backed only by an injected registered broker."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import importlib
import math
import time
from typing import Protocol, runtime_checkable


# This service name is intentionally narrower than a general OpenAI credential scope.
OPENAI_SERVICE = "openhands_subscription"
OPENAI_SUBSCRIPTION_VENDOR = "openai"


class OpenHandsCredentialStoreError(RuntimeError):
    """A deliberately non-diagnostic credential-store failure."""


@dataclass(frozen=True, slots=True, repr=False)
class SubscriptionCredentialRecord:
    """Versioned broker value; ``expires_at`` is epoch milliseconds."""

    service: str
    vendor: str
    access_token: str
    refresh_token: str
    expires_at: int
    revision: str | None = None

    def __repr__(self) -> str:
        return "SubscriptionCredentialRecord(<redacted>)"


@runtime_checkable
class RegisteredSubscriptionCredentialTransport(Protocol):
    def load(self, *, service: str, vendor: str) -> SubscriptionCredentialRecord | None: ...

    def store_atomic(self, record: SubscriptionCredentialRecord) -> None: ...

    def delete(self, *, service: str, vendor: str) -> bool: ...

    def rotate_atomic(
        self, record: SubscriptionCredentialRecord, *, expected_revision: str
    ) -> bool: ...


class OpenHandsSubscriptionCredentialStore:
    """OpenAI-only implementation of OpenHands SDK 1.21.0 ``CredentialStore``."""

    def __init__(
        self,
        transport: RegisteredSubscriptionCredentialTransport | None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if transport is None or any(
            not callable(getattr(transport, name, None))
            for name in ("load", "store_atomic", "delete", "rotate_atomic")
        ):
            raise OpenHandsCredentialStoreError("credential_transport_unavailable")
        if not callable(clock):
            raise OpenHandsCredentialStoreError("credential_clock_invalid")
        self._transport = transport
        self._clock = clock

    def __repr__(self) -> str:
        return "OpenHandsSubscriptionCredentialStore(<registered transport>)"

    def get(self, vendor: str) -> object | None:
        self._require_vendor(vendor)
        record = self._load()
        if record is None:
            return None
        self._validate_record(record, require_revision=False)
        return self._credential(record)

    def save(self, credentials: object) -> None:
        model = self._credential_type()
        if not isinstance(credentials, model):
            raise OpenHandsCredentialStoreError("credential_record_invalid")
        vendor = self._safe_field(credentials, "vendor")
        self._require_vendor(vendor)
        record = SubscriptionCredentialRecord(
            service=OPENAI_SERVICE,
            vendor=OPENAI_SUBSCRIPTION_VENDOR,
            access_token=self._token(self._safe_field(credentials, "access_token")),
            refresh_token=self._token(self._safe_field(credentials, "refresh_token")),
            expires_at=self._expiration(self._safe_field(credentials, "expires_at")),
        )
        self._validate_record(record, require_revision=False)
        if record.expires_at <= self._now_ms():
            raise OpenHandsCredentialStoreError("credential_expiration_invalid")
        failed = False
        try:
            self._transport.store_atomic(record)
        except Exception:
            failed = True
        if failed:
            raise OpenHandsCredentialStoreError("credential_store_failed")

    def delete(self, vendor: str) -> bool:
        self._require_vendor(vendor)
        failed = False
        deleted: object = None
        try:
            deleted = self._transport.delete(service=OPENAI_SERVICE, vendor=vendor)
        except Exception:
            failed = True
        if failed:
            raise OpenHandsCredentialStoreError("credential_delete_failed")
        if not isinstance(deleted, bool):
            raise OpenHandsCredentialStoreError("credential_delete_failed")
        return deleted

    def update_tokens(
        self,
        vendor: str,
        access_token: str,
        refresh_token: str | None,
        expires_in: int,
    ) -> object | None:
        self._require_vendor(vendor)
        new_access = self._token(access_token)
        if refresh_token is not None:
            new_refresh = self._token(refresh_token)
        else:
            new_refresh = None
        duration = self._duration(expires_in)
        now_ms = self._now_ms()

        current = self._load()
        if current is None:
            return None
        self._validate_record(current, require_revision=True)
        replacement = SubscriptionCredentialRecord(
            service=OPENAI_SERVICE,
            vendor=OPENAI_SUBSCRIPTION_VENDOR,
            access_token=new_access,
            refresh_token=new_refresh or current.refresh_token,
            expires_at=now_ms + duration * 1000,
        )
        failed = False
        rotated = False
        try:
            rotated = self._transport.rotate_atomic(
                replacement, expected_revision=current.revision or ""
            )
        except Exception:
            failed = True
        if failed or rotated is not True:
            raise OpenHandsCredentialStoreError("credential_rotation_failed")
        return self._credential(replacement)

    def _load(self) -> SubscriptionCredentialRecord | None:
        failed = False
        record: object = None
        try:
            record = self._transport.load(
                service=OPENAI_SERVICE, vendor=OPENAI_SUBSCRIPTION_VENDOR
            )
        except Exception:
            failed = True
        if failed:
            raise OpenHandsCredentialStoreError("credential_access_denied")
        if record is not None and not isinstance(record, SubscriptionCredentialRecord):
            raise OpenHandsCredentialStoreError("credential_record_invalid")
        return record

    def _credential(self, record: SubscriptionCredentialRecord) -> object:
        model = self._credential_type()
        failed = False
        credential: object = None
        try:
            class RedactedOAuthCredentials(model):
                def __repr__(self) -> str:
                    return "OAuthCredentials(<redacted>)"

                def __str__(self) -> str:
                    return "OAuthCredentials(<redacted>)"

            credential = RedactedOAuthCredentials(
                vendor=record.vendor,
                access_token=record.access_token,
                refresh_token=record.refresh_token,
                expires_at=record.expires_at,
            )
        except Exception:
            failed = True
        if failed:
            raise OpenHandsCredentialStoreError("credential_record_invalid")
        return credential

    @staticmethod
    def _credential_type() -> type:
        failed = False
        model: object = None
        try:
            module = importlib.import_module("openhands.sdk.llm.auth.credentials")
            model = getattr(module, "OAuthCredentials")
        except Exception:
            failed = True
        if failed:
            raise OpenHandsCredentialStoreError("openhands_sdk_unavailable")
        if not isinstance(model, type):
            raise OpenHandsCredentialStoreError("openhands_sdk_unavailable")
        return model

    def _validate_record(
        self, record: SubscriptionCredentialRecord, *, require_revision: bool
    ) -> None:
        if record.service != OPENAI_SERVICE or record.vendor != OPENAI_SUBSCRIPTION_VENDOR:
            raise OpenHandsCredentialStoreError("credential_scope_mismatch")
        self._token(record.access_token)
        self._token(record.refresh_token)
        self._expiration(record.expires_at)
        if require_revision and (not isinstance(record.revision, str) or not record.revision):
            raise OpenHandsCredentialStoreError("credential_record_invalid")

    @staticmethod
    def _require_vendor(vendor: object) -> None:
        if vendor != OPENAI_SUBSCRIPTION_VENDOR:
            raise OpenHandsCredentialStoreError("credential_vendor_unsupported")

    def _now_ms(self) -> int:
        failed = False
        value = 0.0
        try:
            value = float(self._clock())
        except (TypeError, ValueError, OverflowError):
            failed = True
        if failed:
            raise OpenHandsCredentialStoreError("credential_clock_invalid")
        if not math.isfinite(value) or value < 0:
            raise OpenHandsCredentialStoreError("credential_clock_invalid")
        return int(value * 1000)

    @staticmethod
    def _safe_field(credentials: object, name: str) -> object:
        failed = False
        value: object = None
        try:
            value = getattr(credentials, name)
        except Exception:
            failed = True
        if failed:
            raise OpenHandsCredentialStoreError("credential_record_invalid")
        return value

    @staticmethod
    def _token(value: object) -> str:
        if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
            raise OpenHandsCredentialStoreError("credential_token_invalid")
        return value

    @staticmethod
    def _expiration(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise OpenHandsCredentialStoreError("credential_expiration_invalid")
        return value

    @staticmethod
    def _duration(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise OpenHandsCredentialStoreError("credential_expiration_invalid")
        return value
