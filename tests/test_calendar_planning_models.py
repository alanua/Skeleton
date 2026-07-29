from __future__ import annotations

import json

import pytest

from core.calendar_planning_models import (
    FIELD_NAMES,
    CalendarBinding,
    CalendarMutationProposal,
    CalendarPlanningValidationError,
    DesiredCalendarEvent,
    hash_calendar_field,
    stable_binding_id,
    stable_event_key,
)


def hashes(start: int = 100, end: int = 200, suffix: str = "a") -> dict[str, str]:
    return {
        "time": hash_calendar_field({"start_at": start, "end_at": end}),
        "title": hash_calendar_field("title-" + suffix),
        "description": hash_calendar_field("description-" + suffix),
        "location": hash_calendar_field("location-" + suffix),
        "attendees": hash_calendar_field(["attendee-" + suffix]),
        "reminders": hash_calendar_field([3600]),
    }


def test_binding_and_event_ids_are_stable() -> None:
    assert stable_binding_id("trip:abc", "travel_primary") == stable_binding_id(
        "trip:abc", "travel_primary"
    )
    assert stable_event_key("trip:abc", "family") != stable_event_key(
        "trip:abc", "travel_primary"
    )
    binding = CalendarBinding.new(
        subject_ref="trip:abc",
        projection_role="travel_primary",
        calendar_ref="calendar:travel",
    )
    assert binding.binding_id == stable_binding_id("trip:abc", "travel_primary")
    assert set(binding.field_ownership) == set(FIELD_NAMES)


def test_desired_event_rejects_mismatched_time_hash() -> None:
    value = hashes()
    value["time"] = "0" * 64
    with pytest.raises(CalendarPlanningValidationError) as exc:
        DesiredCalendarEvent(
            subject_ref="trip:abc",
            projection_role="travel_primary",
            source_revision=1,
            start_at=100,
            end_at=200,
            desired_status="ACTIVE",
            field_hashes=value,
            private_payload_ref="private:payload",
            confirmed=False,
        )
    assert exc.value.reason_code == "TIME_HASH_MISMATCH"


def test_public_receipt_excludes_private_identifiers() -> None:
    proposal = CalendarMutationProposal(
        action="UPDATE",
        reason_codes=("DESCRIPTION_UPDATE",),
        binding_id="calbind_" + "a" * 32,
        event_key="calevent_" + "b" * 32,
        projection_role="travel_primary",
        expected_remote_revision="etag:private",
        source_revision=2,
        changed_fields=("description",),
        private_payload_ref="private:trip-payload",
        operator_required=False,
    )
    receipt = proposal.public_receipt()
    rendered = json.dumps(receipt)
    assert "private:trip-payload" not in rendered
    assert "etag:private" not in rendered
    assert "calbind_" not in rendered
    assert receipt["private_identifiers_included"] is False
    assert proposal.deterministic_hash() == proposal.deterministic_hash()
