from __future__ import annotations

from core.family_document_taxonomy import classify_family_document_text


def test_exact_appointment_date_and_time_produces_typed_event() -> None:
    result = classify_family_document_text(
        "Jobcenter Termin Einladung für Alex am 31.08.2026 um 10:30. Bitte erscheinen.",
        ("Alex", "Maria", "Ivan"),
    )

    events = result["event_candidates"]
    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == "appointment"
    assert event["timezone"] == "Europe/Berlin"
    assert isinstance(event["start_at"], int)
    assert event["title"] == "Termin — Jobcenter"
    assert "CALENDAR_EVENT_AMBIGUOUS" not in result["reason_codes"]


def test_appointment_without_exact_time_does_not_fabricate_event_and_routes_review() -> None:
    result = classify_family_document_text(
        "Jobcenter Termin Einladung für Alex am 31.08.2026. Bitte erscheinen.",
        ("Alex", "Maria", "Ivan"),
    )

    assert result["event_candidates"] == []
    assert result["route"] == "REVIEW"
    assert "CALENDAR_EVENT_AMBIGUOUS" in result["reason_codes"]


def test_non_appointment_document_date_does_not_become_calendar_event() -> None:
    result = classify_family_document_text(
        "Finanzamt Bescheid für Alex vom 31.08.2026. Steuer Entscheidung.",
        ("Alex", "Maria", "Ivan"),
    )

    assert result["event_candidates"] == []
    assert "CALENDAR_EVENT_AMBIGUOUS" not in result["reason_codes"]
