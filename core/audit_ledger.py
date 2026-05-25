from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


AUDIT_EVENT_SCHEMA = "skeleton.memory_event.v1"
MAX_EVENT_STRING_LENGTH = 4000
_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "auth_token",
    "client_secret",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|ghp|github_pat)_[A-Za-z0-9_=-]{16,}\b"),
    re.compile(r"\b[A-Za-z0-9_]*token[A-Za-z0-9_]*\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9_]*password[A-Za-z0-9_]*\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9_]*secret[A-Za-z0-9_]*\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9_]*api[_-]?key[A-Za-z0-9_]*\s*[:=]\s*\S+", re.IGNORECASE),
)
_DRIVE_PATTERNS = (
    re.compile(r"https?://(?:drive|docs)\.google\.com/", re.IGNORECASE),
    re.compile(r"\b(?:drive_file_id|google_drive_id|file_id)\b", re.IGNORECASE),
)
_PRIVATE_PATH_PATTERN = re.compile(
    r"(?:(?:^|\s)/(?:home|root|mnt|media|var/lib|etc|run/secrets)/|[A-Za-z]:\\Users\\)"
)
_ENV_LINE_PATTERN = re.compile(
    r"(?m)^[A-Z][A-Z0-9_]{2,}\s*=\s*.+$"
)


class AuditLedger:
    """Append-only JSONL audit ledger for public-safe operational events."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, event: dict[str, Any]) -> None:
        normalized = sanitized_event(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            normalized,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with self.path.open("a", encoding="utf-8", newline="\n") as ledger:
            ledger.write(payload)
            ledger.write("\n")

    def read_recent(self, n: int) -> list[dict[str, Any]]:
        if n <= 0 or not self.path.exists():
            return []

        rows = self.path.read_text(encoding="utf-8").splitlines()
        recent: list[dict[str, Any]] = []
        for line in rows[-n:]:
            if line.strip():
                recent.append(json.loads(line))
        return recent

    def rotate_if_needed(self, max_size_mb: int = 50) -> None:
        if max_size_mb < 0:
            raise ValueError("max_size_mb must be non-negative.")
        if not self.path.exists():
            return

        max_bytes = max_size_mb * 1024 * 1024
        if self.path.stat().st_size <= max_bytes:
            return

        rotated_path = self._next_rotated_path()
        self.path.rename(rotated_path)
        self.path.touch(exist_ok=False)

    def _next_rotated_path(self) -> Path:
        timestamp = _utc_now().replace(":", "").replace("-", "")
        candidate = self.path.with_name(f"{self.path.name}.{timestamp}.rotated")
        if not candidate.exists():
            return candidate
        return self.path.with_name(f"{self.path.name}.{timestamp}.{uuid.uuid4().hex}.rotated")


def sanitized_event(event: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise TypeError("event must be a mapping.")

    normalized = _normalize_json_value(event, path="event")
    if not isinstance(normalized, dict):
        raise TypeError("event must normalize to a JSON object.")

    normalized.setdefault("schema", AUDIT_EVENT_SCHEMA)
    normalized.setdefault("id", str(uuid.uuid4()))
    normalized.setdefault("created_at", _utc_now())
    validate_public_safe_payload(normalized)
    return normalized


def validate_public_safe_payload(payload: Mapping[str, Any]) -> None:
    normalized = _normalize_json_value(payload, path="payload")
    _reject_unsafe_value(normalized, path="payload")
    json.dumps(normalized, ensure_ascii=True, allow_nan=False, sort_keys=True)


def _normalize_json_value(value: Any, *, path: str) -> Any:
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings.")
            normalized[key] = _normalize_json_value(child, path=f"{path}.{key}")
        return normalized

    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(child, path=f"{path}[]") for child in value]

    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    raise ValueError(f"{path} contains a non-JSON-safe value.")


def _reject_unsafe_value(value: Any, *, path: str) -> None:
    if isinstance(value, Mapping):
        event_type = value.get("event_type") or value.get("type")
        for key, child in value.items():
            lowered = key.lower()
            if any(part in lowered for part in _SECRET_KEY_PARTS):
                raise ValueError(f"{path}.{key} looks like a secret field.")
            if event_type == "private_reference_stub" and lowered in {"content", "path", "url", "file_id"}:
                raise ValueError("private_reference_stub must be opaque and must not include raw content or locators.")
            if any(pattern.search(key) for pattern in _DRIVE_PATTERNS):
                raise ValueError(f"{path}.{key} looks like a private Drive reference.")
            _reject_unsafe_value(child, path=f"{path}.{key}")
        return

    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_unsafe_value(child, path=f"{path}[{index}]")
        return

    if isinstance(value, str):
        if len(value) > MAX_EVENT_STRING_LENGTH:
            raise ValueError(f"{path} looks like an unbounded raw log.")
        if any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS):
            raise ValueError(f"{path} looks like secret material.")
        if any(pattern.search(value) for pattern in _DRIVE_PATTERNS):
            raise ValueError(f"{path} looks like a private Drive reference.")
        if _PRIVATE_PATH_PATTERN.search(value):
            raise ValueError(f"{path} looks like a raw private path.")
        if _ENV_LINE_PATTERN.search(value):
            raise ValueError(f"{path} looks like .env content.")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
