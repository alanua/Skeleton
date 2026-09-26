from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping


TELEGRAM_AUDIT_SCHEMA = "skeleton.telegram_gateway.audit_event.v1"
SECRET_MARKERS = ("token", "secret", "session", "hash", "api_id", "api_hash")


@dataclass
class TelegramAuditLog:
    events: list[dict[str, object]] = field(default_factory=list)

    def record(
        self,
        *,
        action: str,
        source_id: str,
        status: str,
        reason_code: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        safe_details = _redact(details or {})
        event = {
            "schema": TELEGRAM_AUDIT_SCHEMA,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "source_id": source_id,
            "status": status,
            "reason_code": reason_code,
            "details": safe_details,
        }
        self.events.append(event)
        return event


def provenance_hash(*parts: object) -> str:
    payload = "|".join(str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _redact(details: Mapping[str, object]) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, value in details.items():
        lowered = key.lower()
        if any(marker in lowered for marker in SECRET_MARKERS):
            redacted[key] = "REDACTED"
        else:
            redacted[key] = value
    return redacted
