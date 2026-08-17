from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from core.scheduler_models import SCHEDULE_SCHEMA, ScheduleSpec
from core.scheduler_store import SchedulerStore


class FamilyDocumentCalendarError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FamilyDocumentCalendarReceipt:
    status: str
    required: bool
    event_count: int
    created_count: int
    duplicate_count: int
    semantic_hashes: tuple[str, ...]

    def to_mapping(self) -> dict[str, object]:
        return {
            "status": self.status,
            "required": self.required,
            "event_count": self.event_count,
            "created_count": self.created_count,
            "duplicate_count": self.duplicate_count,
            "semantic_hashes": list(self.semantic_hashes),
        }


class FamilyDocumentCalendar(Protocol):
    def upsert(self, record: Mapping[str, Any]) -> FamilyDocumentCalendarReceipt: ...


class NoopFamilyDocumentCalendar:
    """Explicit no-event sink; valid only when the record has no event candidates."""

    def upsert(self, record: Mapping[str, Any]) -> FamilyDocumentCalendarReceipt:
        candidates = _event_candidates(record)
        if candidates:
            raise FamilyDocumentCalendarError("calendar sink required for event candidates")
        return FamilyDocumentCalendarReceipt("NO_EVENT", False, 0, 0, 0, ())


class SchedulerFamilyDocumentCalendar:
    """Stores document-derived event semantics in Skeleton Scheduler authority.

    Google/Microsoft calendars remain mirrors. This adapter never calls them directly.
    """

    def __init__(self, store: SchedulerStore, *, clock: Callable[[], int] | None = None) -> None:
        self.store = store
        self.clock = clock or (lambda: int(time.time()))

    def upsert(self, record: Mapping[str, Any]) -> FamilyDocumentCalendarReceipt:
        candidates = _event_candidates(record)
        if not candidates:
            return FamilyDocumentCalendarReceipt("NO_EVENT", False, 0, 0, 0, ())
        record_id = str(record.get("record_id") or "")
        if not record_id:
            raise FamilyDocumentCalendarError("record id required")
        created = 0
        duplicates = 0
        semantic_hashes: list[str] = []
        now = int(self.clock())
        for candidate in candidates:
            normalized = _normalize_candidate(candidate)
            semantic_hash = _semantic_hash(record_id, normalized)
            semantic_hashes.append(semantic_hash)
            spec = ScheduleSpec.from_mapping(
                {
                    "schema": SCHEDULE_SCHEMA,
                    "schedule_id": f"family-document-{semantic_hash[:32]}",
                    "trigger_kind": "once",
                    "cron_expression": None,
                    "once_at": normalized["start_at"],
                    "timezone": normalized["timezone"],
                    "route_type": "notify",
                    "route_id": "family_document_calendar_event",
                    "approval_policy": "notify_only",
                    "overlap_policy": "skip",
                    "misfire_policy": "run_once",
                    "payload": {
                        "source": "family_document",
                        "record_id": record_id,
                        "event_type": normalized["event_type"],
                        "title": normalized["title"],
                        "end_at": normalized.get("end_at"),
                    },
                }
            )
            _stored, was_created = self.store.register(spec, now=now, enabled=True)
            if was_created:
                created += 1
            else:
                duplicates += 1
        return FamilyDocumentCalendarReceipt(
            "DONE",
            True,
            len(candidates),
            created,
            duplicates,
            tuple(semantic_hashes),
        )


def _event_candidates(record: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    classification = record.get("classification")
    if not isinstance(classification, Mapping):
        return ()
    raw = classification.get("event_candidates", [])
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise FamilyDocumentCalendarError("event candidates malformed")
    candidates: list[Mapping[str, Any]] = []
    for value in raw:
        if not isinstance(value, Mapping):
            raise FamilyDocumentCalendarError("event candidate malformed")
        candidates.append(value)
    return tuple(candidates)


def _normalize_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"event_type", "title", "start_at", "end_at", "timezone"}
    if set(candidate) - allowed:
        raise FamilyDocumentCalendarError("event candidate unknown field")
    start_at = candidate.get("start_at")
    if isinstance(start_at, bool) or not isinstance(start_at, int) or start_at < 0:
        raise FamilyDocumentCalendarError("event start invalid")
    end_at = candidate.get("end_at")
    if end_at is not None and (isinstance(end_at, bool) or not isinstance(end_at, int) or end_at < start_at):
        raise FamilyDocumentCalendarError("event end invalid")
    timezone_name = candidate.get("timezone")
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise FamilyDocumentCalendarError("event timezone invalid")
    event_type = candidate.get("event_type")
    if not isinstance(event_type, str) or not event_type.strip():
        raise FamilyDocumentCalendarError("event type invalid")
    title = candidate.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 512:
        raise FamilyDocumentCalendarError("event title invalid")
    return {
        "event_type": " ".join(event_type.split()),
        "title": " ".join(title.split()),
        "start_at": start_at,
        "end_at": end_at,
        "timezone": timezone_name.strip(),
    }


def _semantic_hash(record_id: str, event: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        {"record_id": record_id, "event": dict(event)},
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
