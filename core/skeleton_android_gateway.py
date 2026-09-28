from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any
from uuid import uuid4


ANDROID_GATEWAY_EVENT_SCHEMA = "skeleton.android_gateway.telemetry_event.v1"
ANDROID_GATEWAY_RECEIPT_SCHEMA = "skeleton.android_gateway.receipt.v1"
ANDROID_GATEWAY_SNAPSHOT_SCHEMA = "skeleton.android_gateway.snapshot.v1"
ANDROID_GATEWAY_CONTRACT_VERSION = "1.0.0"
ANDROID_GATEWAY_PRIVACY_BOUNDARY = "public_safe_metadata_only"
ANDROID_GATEWAY_RUNTIME_MUTATION = False

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_KEY_TOKEN_RE = re.compile(r"[_.:-]+")
_MAX_EVENTS = 500
_MAX_KEY_DEPTH = 4
_MAX_STRING_LENGTH = 512

_SENSITIVE_KEY_PARTS = frozenset(
    {
        "account",
        "address",
        "auth",
        "body",
        "clipboard",
        "contact",
        "cookie",
        "credential",
        "email",
        "fcm",
        "hostname",
        "imei",
        "imsi",
        "lat",
        "location",
        "lon",
        "mac",
        "message",
        "notification",
        "password",
        "phone",
        "private",
        "secret",
        "serial",
        "sms",
        "ssid",
        "subject",
        "text",
        "token",
        "user",
    }
)

_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "app_content",
        "content",
        "device_runtime",
        "files",
        "intents",
        "mutations",
        "raw_logcat",
        "runtime",
    }
)

_ALLOWED_EVENT_KEYS = frozenset({"schema", "event_id", "provider", "event_type", "observed_at", "payload"})

_FORBIDDEN_KEY_PARTS = frozenset(
    {
        "content",
        "files",
        "intents",
        "mutations",
        "runtime",
    }
)

_FORBIDDEN_KEY_FRAGMENTS = frozenset(
    {
        "appcontent",
        "deviceruntime",
        "intent",
        "mutation",
        "rawlogcat",
    }
)

_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "access_token",
        "android_id",
        "exact_alarm",
    }
)

_SENSITIVE_KEY_FRAGMENTS = frozenset(
    {
        "androidid",
        "clipboard",
        "cookie",
        "credential",
        "email",
        "exactalarm",
        "imei",
        "imsi",
        "location",
        "message",
        "notification",
        "password",
        "phone",
        "rawlogcat",
        "secret",
        "serial",
        "sms",
        "ssid",
        "token",
    }
)


class AndroidGatewayDecision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class AndroidGatewayError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class AndroidTelemetryEvent:
    provider: str
    event_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    observed_at: str | None = None
    event_id: str = field(default_factory=lambda: f"android-telemetry-{uuid4()}")
    schema: str = ANDROID_GATEWAY_EVENT_SCHEMA

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "event_id": self.event_id,
            "provider": self.provider,
            "event_type": self.event_type,
            "observed_at": self.observed_at,
            "payload": dict(self.payload),
        }


class SkeletonAndroidGateway:
    """Provider-neutral Android telemetry intake with a public-safe boundary."""

    def __init__(self, *, clock: Any | None = None, max_events: int = _MAX_EVENTS) -> None:
        if max_events < 1:
            raise ValueError("max_events must be at least 1")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_events = max_events
        self._events: list[dict[str, Any]] = []
        self._rejections: list[dict[str, Any]] = []

    def ingest(self, event: AndroidTelemetryEvent | Mapping[str, Any]) -> dict[str, Any]:
        normalized = _normalize_event(event, now=self._now_iso())
        receipt = {
            "schema": ANDROID_GATEWAY_RECEIPT_SCHEMA,
            "contract_version": ANDROID_GATEWAY_CONTRACT_VERSION,
            "decision": AndroidGatewayDecision.ACCEPTED.value,
            "event_id": normalized["event_id"],
            "provider": normalized["provider"],
            "event_type": normalized["event_type"],
            "observed_at": normalized["observed_at"],
            "digest": normalized["digest"],
            "privacy_boundary": ANDROID_GATEWAY_PRIVACY_BOUNDARY,
            "runtime_mutation": ANDROID_GATEWAY_RUNTIME_MUTATION,
            "reason": "public_safe_provider_neutral_telemetry",
        }
        if len(self._events) >= self._max_events:
            self._events.pop(0)
        self._events.append(normalized)
        return receipt

    def try_ingest(self, event: AndroidTelemetryEvent | Mapping[str, Any]) -> dict[str, Any]:
        try:
            return self.ingest(event)
        except AndroidGatewayError as exc:
            now = self._now_iso()
            rejection = {
                "schema": ANDROID_GATEWAY_RECEIPT_SCHEMA,
                "contract_version": ANDROID_GATEWAY_CONTRACT_VERSION,
                "decision": AndroidGatewayDecision.REJECTED.value,
                "event_id": _safe_event_id(event),
                "provider": _safe_optional_string(event, "provider", pattern=_SAFE_ID_RE),
                "event_type": _safe_optional_string(event, "event_type", pattern=_SAFE_NAME_RE),
                "observed_at": now,
                "privacy_boundary": ANDROID_GATEWAY_PRIVACY_BOUNDARY,
                "runtime_mutation": ANDROID_GATEWAY_RUNTIME_MUTATION,
                "reason": exc.reason_code,
            }
            self._rejections.append(rejection)
            return rejection

    def snapshot(self) -> dict[str, Any]:
        provider_counts: dict[str, int] = {}
        event_type_counts: dict[str, int] = {}
        for event in self._events:
            provider_counts[event["provider"]] = provider_counts.get(event["provider"], 0) + 1
            event_type_counts[event["event_type"]] = event_type_counts.get(event["event_type"], 0) + 1

        return {
            "schema": ANDROID_GATEWAY_SNAPSHOT_SCHEMA,
            "contract_version": ANDROID_GATEWAY_CONTRACT_VERSION,
            "generated_at": self._now_iso(),
            "privacy_boundary": ANDROID_GATEWAY_PRIVACY_BOUNDARY,
            "runtime_mutation": ANDROID_GATEWAY_RUNTIME_MUTATION,
            "event_count": len(self._events),
            "rejection_count": len(self._rejections),
            "providers": dict(sorted(provider_counts.items())),
            "event_types": dict(sorted(event_type_counts.items())),
            "events": list(self._events),
            "rejections": list(self._rejections),
        }

    def _now_iso(self) -> str:
        value = self._clock()
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
        return str(value)


def snapshot_from_events(events: Sequence[AndroidTelemetryEvent | Mapping[str, Any]]) -> dict[str, Any]:
    gateway = SkeletonAndroidGateway()
    for event in events:
        gateway.try_ingest(event)
    return gateway.snapshot()


def event_digest(event: Mapping[str, Any]) -> str:
    encoded = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return sha256(encoded).hexdigest()


def _normalize_event(event: AndroidTelemetryEvent | Mapping[str, Any], *, now: str) -> dict[str, Any]:
    raw = event.to_mapping() if isinstance(event, AndroidTelemetryEvent) else dict(event)
    if raw.get("schema") != ANDROID_GATEWAY_EVENT_SCHEMA:
        raise AndroidGatewayError("INVALID_SCHEMA", "event schema is invalid")
    unknown_keys = set(raw) - _ALLOWED_EVENT_KEYS
    if unknown_keys:
        raise AndroidGatewayError("UNSUPPORTED_EVENT_FIELD", "event contains fields outside the v1 metadata contract")

    event_id = _safe_required_string(raw, "event_id", pattern=_SAFE_ID_RE)
    provider = _safe_required_string(raw, "provider", pattern=_SAFE_ID_RE)
    event_type = _safe_required_string(raw, "event_type", pattern=_SAFE_NAME_RE)
    payload = raw.get("payload", {})
    if not isinstance(payload, Mapping):
        raise AndroidGatewayError("INVALID_PAYLOAD", "payload must be an object")

    sanitized_payload = _sanitize_payload(payload)
    observed_at = raw.get("observed_at") or now
    if not isinstance(observed_at, str) or len(observed_at) > _MAX_STRING_LENGTH:
        raise AndroidGatewayError("INVALID_OBSERVED_AT", "observed_at must be a bounded string")

    return {
        "schema": ANDROID_GATEWAY_EVENT_SCHEMA,
        "event_id": event_id,
        "provider": provider,
        "event_type": event_type,
        "observed_at": observed_at,
        "payload": sanitized_payload,
        "digest": event_digest(
            {
                "event_id": event_id,
                "provider": provider,
                "event_type": event_type,
                "observed_at": observed_at,
                "payload": sanitized_payload,
            }
        ),
    }


def _sanitize_payload(payload: Mapping[str, Any], *, depth: int = 0) -> dict[str, Any]:
    if depth > _MAX_KEY_DEPTH:
        raise AndroidGatewayError("PAYLOAD_TOO_DEEP", "payload nesting is too deep")
    sanitized: dict[str, Any] = {}
    for key, value in sorted(payload.items(), key=lambda item: str(item[0])):
        if not isinstance(key, str) or not _SAFE_NAME_RE.fullmatch(key):
            raise AndroidGatewayError("INVALID_PAYLOAD_KEY", "payload keys must be safe bounded identifiers")
        if _is_sensitive_payload_key(key):
            raise AndroidGatewayError("SENSITIVE_PAYLOAD_REJECTED", "payload contains sensitive app or device content")
        sanitized[key] = _sanitize_value(value, depth=depth)
    return sanitized


def _sanitize_value(value: Any, *, depth: int) -> Any:
    if depth > _MAX_KEY_DEPTH:
        raise AndroidGatewayError("PAYLOAD_TOO_DEEP", "payload nesting is too deep")
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AndroidGatewayError("UNSUPPORTED_PAYLOAD_VALUE", "payload numbers must be finite")
        return value
    if isinstance(value, str):
        if len(value) > _MAX_STRING_LENGTH:
            raise AndroidGatewayError("PAYLOAD_STRING_TOO_LONG", "payload strings must be bounded")
        return value
    if isinstance(value, Mapping):
        return _sanitize_payload(value, depth=depth + 1)
    if isinstance(value, list | tuple):
        if len(value) > 100:
            raise AndroidGatewayError("PAYLOAD_LIST_TOO_LONG", "payload lists must be bounded")
        return [_sanitize_value(item, depth=depth + 1) for item in value]
    raise AndroidGatewayError("UNSUPPORTED_PAYLOAD_VALUE", "payload values must be JSON-safe primitives")


def _safe_required_string(raw: Mapping[str, Any], key: str, *, pattern: re.Pattern[str]) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise AndroidGatewayError(f"INVALID_{key.upper()}", f"{key} must be a safe bounded string")
    return value


def _is_sensitive_payload_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _FORBIDDEN_PAYLOAD_KEYS or lowered in _SENSITIVE_EXACT_KEYS:
        return True
    compact = re.sub(r"[_.:-]+", "", lowered)
    if any(fragment in compact for fragment in _FORBIDDEN_KEY_FRAGMENTS):
        return True
    if any(fragment in compact for fragment in _SENSITIVE_KEY_FRAGMENTS):
        return True
    parts = _KEY_TOKEN_RE.split(lowered)
    return any(part in _FORBIDDEN_KEY_PARTS or part in _SENSITIVE_KEY_PARTS for part in parts)


def _safe_event_id(event: AndroidTelemetryEvent | Mapping[str, Any]) -> str | None:
    raw = event.to_mapping() if isinstance(event, AndroidTelemetryEvent) else event
    value = raw.get("event_id") if isinstance(raw, Mapping) else None
    return value if isinstance(value, str) and _SAFE_ID_RE.fullmatch(value) else None


def _safe_optional_string(
    event: AndroidTelemetryEvent | Mapping[str, Any], key: str, *, pattern: re.Pattern[str]
) -> str | None:
    raw = event.to_mapping() if isinstance(event, AndroidTelemetryEvent) else event
    value = raw.get(key) if isinstance(raw, Mapping) else None
    return value if isinstance(value, str) and pattern.fullmatch(value) else None
