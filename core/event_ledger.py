from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import re
from typing import Any, Mapping


EVENT_LEDGER_SCHEMA = "skeleton.event_ledger.event.v1"

ALLOWED_EVENT_STATUSES = frozenset({"started", "completed", "blocked", "failed", "skipped", "recorded"})
MAX_IDENTIFIER_CHARS = 64
MAX_ACTOR_REFERENCE_CHARS = 96
MAX_SUMMARY_CHARS = 240
MAX_ATTRIBUTE_STRING_CHARS = 240
MAX_ATTRIBUTE_KEYS = 24
MAX_ATTRIBUTE_DEPTH = 4

_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_ACTOR_REFERENCE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9@._/-]{1,63}$")
_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_UNSAFE_ATTRIBUTE_KEYS = frozenset(
    {
        "api_key",
        "content",
        "credential",
        "credentials",
        "password",
        "private",
        "private_key",
        "raw_content",
        "secret",
        "secrets",
        "source",
        "source_text",
        "token",
        "tokens",
    }
)


class _FrozenMapping(tuple):
    pass


@dataclass(frozen=True)
class EventLedgerEvent:
    """Immutable public-safe workflow event for later ledger storage."""

    workflow_id: str
    event_id: str
    event_type: str
    status: str
    actor_reference: str
    timestamp: str
    summary: str
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workflow_id", validate_identifier(self.workflow_id, "workflow_id"))
        object.__setattr__(self, "event_id", validate_identifier(self.event_id, "event_id"))
        object.__setattr__(self, "event_type", validate_identifier(self.event_type, "event_type"))
        object.__setattr__(self, "status", validate_status(self.status))
        object.__setattr__(self, "actor_reference", validate_actor_reference(self.actor_reference))
        object.__setattr__(self, "timestamp", validate_utc_timestamp(self.timestamp))
        object.__setattr__(self, "summary", validate_summary(self.summary))
        object.__setattr__(self, "attributes", _freeze_attributes(self.attributes))

    def to_dict(self) -> dict[str, object]:
        return event_ledger_event_to_dict(self)

    def to_json(self) -> str:
        return event_ledger_event_to_json(self)


def event_ledger_event_to_dict(event: EventLedgerEvent) -> dict[str, object]:
    """Return the fixed JSON-compatible event field order."""
    if not isinstance(event, EventLedgerEvent):
        raise TypeError("event must be an EventLedgerEvent.")

    return {
        "schema": EVENT_LEDGER_SCHEMA,
        "workflow_id": event.workflow_id,
        "event_id": event.event_id,
        "event_type": event.event_type,
        "status": event.status,
        "actor_reference": event.actor_reference,
        "timestamp": event.timestamp,
        "summary": event.summary,
        "attributes": _thaw_attributes(event.attributes),
    }


def event_ledger_event_to_json(event: EventLedgerEvent) -> str:
    """Serialize an event as compact deterministic JSON with no trailing newline."""
    return json.dumps(
        event_ledger_event_to_dict(event),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def validate_identifier(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > MAX_IDENTIFIER_CHARS
        or _IDENTIFIER_RE.fullmatch(value) is None
    ):
        raise ValueError(f"{field_name} must be a bounded public-safe identifier.")
    return value


def validate_status(status: object) -> str:
    if status not in ALLOWED_EVENT_STATUSES:
        raise ValueError("status is not supported.")
    return status


def validate_actor_reference(actor_reference: object) -> str:
    if (
        not isinstance(actor_reference, str)
        or len(actor_reference) > MAX_ACTOR_REFERENCE_CHARS
        or _ACTOR_REFERENCE_RE.fullmatch(actor_reference) is None
    ):
        raise ValueError("actor_reference must be a bounded public-safe actor reference.")
    return actor_reference


def validate_utc_timestamp(timestamp: object) -> str:
    if not isinstance(timestamp, str) or _UTC_TIMESTAMP_RE.fullmatch(timestamp) is None:
        raise ValueError("timestamp must be a UTC RFC3339 second timestamp.")
    try:
        parsed = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError("timestamp must be a UTC RFC3339 second timestamp.") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("timestamp must be a UTC RFC3339 second timestamp.")
    return timestamp


def validate_summary(summary: object) -> str:
    if not isinstance(summary, str):
        raise ValueError("summary must be text.")
    normalized = " ".join(summary.split())
    if not normalized:
        raise ValueError("summary must not be empty.")
    if len(normalized) > MAX_SUMMARY_CHARS:
        raise ValueError(f"summary must be at most {MAX_SUMMARY_CHARS} characters.")
    return normalized


def _freeze_attributes(attributes: object) -> _FrozenMapping:
    if not isinstance(attributes, Mapping):
        raise ValueError("attributes must be a mapping.")
    if len(attributes) > MAX_ATTRIBUTE_KEYS:
        raise ValueError(f"attributes must contain at most {MAX_ATTRIBUTE_KEYS} keys.")
    frozen = _freeze_json_value(attributes, path="attributes", depth=0)
    if not isinstance(frozen, tuple):
        raise ValueError("attributes must be a mapping.")
    return frozen


def _freeze_json_value(value: object, *, path: str, depth: int) -> Any:
    if depth > MAX_ATTRIBUTE_DEPTH:
        raise ValueError(f"{path} exceeds maximum attribute depth.")

    if isinstance(value, Mapping):
        if len(value) > MAX_ATTRIBUTE_KEYS:
            raise ValueError(f"{path} must contain at most {MAX_ATTRIBUTE_KEYS} keys.")
        normalized: list[tuple[str, Any]] = []
        for key, child in value.items():
            if not isinstance(key, str) or _IDENTIFIER_RE.fullmatch(key) is None:
                raise ValueError(f"{path} keys must be public-safe identifiers.")
            if key.lower() in _UNSAFE_ATTRIBUTE_KEYS:
                raise ValueError(f"attributes must not store unsafe field: {key}")
            normalized.append((key, _freeze_json_value(child, path=f"{path}.{key}", depth=depth + 1)))
        return _FrozenMapping(sorted(normalized, key=lambda item: item[0]))

    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json_value(child, path=f"{path}[]", depth=depth + 1)
            for child in value
        )

    if isinstance(value, str):
        normalized = " ".join(value.split())
        if len(normalized) > MAX_ATTRIBUTE_STRING_CHARS:
            raise ValueError(f"{path} strings must be at most {MAX_ATTRIBUTE_STRING_CHARS} characters.")
        return normalized

    if value is None or isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain finite JSON numbers.")
        return value

    raise ValueError(f"{path} contains a non-JSON-safe value.")


def _thaw_attributes(value: Any) -> Any:
    if _is_frozen_mapping(value):
        return {key: _thaw_attributes(child) for key, child in value}
    if isinstance(value, tuple):
        return [_thaw_attributes(child) for child in value]
    return value


def _is_frozen_mapping(value: Any) -> bool:
    return isinstance(value, _FrozenMapping)
