from __future__ import annotations

import pytest

from core.family_document_calendar import (
    FamilyDocumentCalendarError,
    NoopFamilyDocumentCalendar,
    SchedulerFamilyDocumentCalendar,
)
from core.scheduler_store import SchedulerStore


def _record() -> dict[str, object]:
    return {
        "record_id": "doc-123",
        "classification": {
            "event_candidates": [
                {
                    "event_type": "appointment",
                    "title": "Termin — Jobcenter",
                    "start_at": 1788170400,
                    "end_at": None,
                    "timezone": "Europe/Berlin",
                }
            ]
        },
    }


def test_scheduler_calendar_upsert_is_idempotent(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    sink = SchedulerFamilyDocumentCalendar(store, clock=lambda: 1786900000)

    first = sink.upsert(_record())
    second = sink.upsert(_record())

    assert first.status == "DONE"
    assert first.required is True
    assert first.event_count == 1
    assert first.created_count == 1
    assert first.duplicate_count == 0
    assert second.created_count == 0
    assert second.duplicate_count == 1
    assert first.semantic_hashes == second.semantic_hashes
    assert len(store.list_enabled()) == 1


def test_no_event_is_explicit_and_does_not_write(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    sink = SchedulerFamilyDocumentCalendar(store, clock=lambda: 1786900000)

    receipt = sink.upsert({"record_id": "doc-1", "classification": {"event_candidates": []}})

    assert receipt.status == "NO_EVENT"
    assert receipt.required is False
    assert store.list_enabled() == ()


def test_noop_sink_fails_closed_when_event_requires_scheduler() -> None:
    with pytest.raises(FamilyDocumentCalendarError):
        NoopFamilyDocumentCalendar().upsert(_record())
