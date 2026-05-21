from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from core.memory_manager import MemoryRecord, MemoryRouteResult


MEMORY_LEDGER_SCHEMA = "skeleton.memory_store.ledger_entry.v1"
SESSION_STATE_SNAPSHOT_SCHEMA = "skeleton.memory_store.session_state_snapshot.v1"
CONTENT_PREVIEW_LIMIT = 160
PRIVATE_CONTENT_PREVIEW = "[REDACTED: non-public memory content]"
_SNAPSHOT_UNSAFE_KEYS = frozenset(
    {
        "content",
        "credential",
        "credentials",
        "memory_content",
        "private_content",
        "raw_content",
        "secret",
        "secrets",
        "token",
        "tokens",
    }
)


@dataclass(frozen=True)
class MemoryLedgerEntry:
    schema: str
    record_id: str
    project_id: str
    memory_type: str
    source: str
    trust_level: str
    record_status: str
    created_at: str
    public_safe: bool
    content_preview: str
    route_status: str
    target_route: str
    requires_operator_approval: bool
    audit_summary: str
    blocked_reason: str | None


def build_memory_ledger_entry(record: MemoryRecord, route: MemoryRouteResult) -> MemoryLedgerEntry:
    """Return a bounded local audit entry for one routed memory record."""
    if not isinstance(record, MemoryRecord):
        raise TypeError("record must be a MemoryRecord from core.memory_manager.")
    if not isinstance(route, MemoryRouteResult):
        raise TypeError("route must be a MemoryRouteResult from core.memory_manager.")

    return MemoryLedgerEntry(
        schema=MEMORY_LEDGER_SCHEMA,
        record_id=record.id,
        project_id=record.project_id,
        memory_type=record.memory_type,
        source=record.source,
        trust_level=record.trust_level,
        record_status=record.status,
        created_at=record.created_at,
        public_safe=record.public_safe,
        content_preview=redact_private_content(record),
        route_status=route.status,
        target_route=route.target_route,
        requires_operator_approval=route.requires_operator_approval,
        audit_summary=route.audit_summary,
        blocked_reason=route.blocked_reason,
    )


def memory_ledger_entry_to_dict(entry: MemoryLedgerEntry) -> dict[str, object]:
    """Return the fixed JSON-compatible ledger entry field order."""
    if not isinstance(entry, MemoryLedgerEntry):
        raise TypeError("entry must be a MemoryLedgerEntry.")

    return {
        "schema": entry.schema,
        "record_id": entry.record_id,
        "project_id": entry.project_id,
        "memory_type": entry.memory_type,
        "source": entry.source,
        "trust_level": entry.trust_level,
        "record_status": entry.record_status,
        "created_at": entry.created_at,
        "public_safe": entry.public_safe,
        "content_preview": entry.content_preview,
        "route_status": entry.route_status,
        "target_route": entry.target_route,
        "requires_operator_approval": entry.requires_operator_approval,
        "audit_summary": entry.audit_summary,
        "blocked_reason": entry.blocked_reason,
    }


def append_memory_ledger_entry(path: str | Path, entry: MemoryLedgerEntry) -> Path:
    """Append one deterministic JSONL entry to an explicit local path."""
    output_path = Path(path)
    payload = json.dumps(
        memory_ledger_entry_to_dict(entry),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    with output_path.open("a", encoding="utf-8", newline="\n") as ledger:
        ledger.write(payload)
        ledger.write("\n")
    return output_path


def write_session_state_snapshot(path: str | Path, snapshot: Mapping[str, Any]) -> Path:
    """Write one deterministic public-safe session/project state JSON snapshot."""
    normalized = _validated_snapshot(snapshot)
    output_path = Path(path)
    payload = json.dumps(
        normalized,
        ensure_ascii=True,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    )
    output_path.write_text(payload + "\n", encoding="utf-8")
    return output_path


def redact_private_content(record: MemoryRecord) -> str:
    """Return a bounded public preview, or a redaction marker for non-public content."""
    if not isinstance(record, MemoryRecord):
        raise TypeError("record must be a MemoryRecord from core.memory_manager.")
    if record.memory_type == "private_sensitive" or not record.public_safe:
        return PRIVATE_CONTENT_PREVIEW
    return _bounded_preview(record.content)


def _bounded_preview(content: str) -> str:
    preview = " ".join(content.split())
    if len(preview) <= CONTENT_PREVIEW_LIMIT:
        return preview
    return preview[: CONTENT_PREVIEW_LIMIT - 3] + "..."


def _validated_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, Mapping):
        raise TypeError("snapshot must be a mapping.")
    if snapshot.get("public_safe") is not True:
        raise ValueError("snapshot public_safe must be true.")

    normalized = _normalize_snapshot_value(snapshot, path="snapshot")
    if not isinstance(normalized, dict):
        raise TypeError("snapshot must normalize to a JSON object.")

    try:
        json.dumps(normalized, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("snapshot must contain deterministic JSON-safe values.") from exc
    return normalized


def _normalize_snapshot_value(value: Any, *, path: str) -> Any:
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings.")
            if key.lower() in _SNAPSHOT_UNSAFE_KEYS:
                raise ValueError(f"snapshot must not store unsafe content field: {key}")
            normalized[key] = _normalize_snapshot_value(child, path=f"{path}.{key}")
        return normalized

    if isinstance(value, (list, tuple)):
        return [_normalize_snapshot_value(child, path=f"{path}[]") for child in value]

    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    raise ValueError(f"{path} contains a non-JSON-safe value.")
