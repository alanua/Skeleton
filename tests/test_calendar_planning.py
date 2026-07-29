from __future__ import annotations

import json

from core.calendar_planning import (
    aggregate_reconcile_receipt,
    find_conflicts,
    find_free_windows,
    reconcile_calendar_event,
)
from core.calendar_planning_models import (
    BusyInterval,
    CalendarBinding,
    DesiredCalendarEvent,
    RemoteCalendarEvent,
    hash_calendar_field,
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


def desired(
    *,
    role: str = "travel_primary",
    suffix: str = "a",
    confirmed: bool = False,
    status: str = "ACTIVE",
    start: int = 100,
    end: int = 200,
    invitation_change: bool = False,
) -> DesiredCalendarEvent:
    return DesiredCalendarEvent(
        subject_ref="trip:abc",
        projection_role=role,
        source_revision=2,
        start_at=start,
        end_at=end,
        desired_status=status,
        field_hashes=hashes(start=start, end=end, suffix=suffix),
        private_payload_ref=f"private:{role}:{suffix}",
        confirmed=confirmed,
        invitation_change=invitation_change,
    )


def bound(
    *,
    role: str = "travel_primary",
    projected: dict[str, str] | None = None,
    ownership: str | None = None,
) -> CalendarBinding:
    event_ownership = ownership or "SKELETON_OWNED"
    fields = {field: event_ownership for field in hashes()}
    return CalendarBinding(
        binding_id=CalendarBinding.new(
            subject_ref="trip:abc",
            projection_role=role,
            calendar_ref=f"calendar:{role}",
            event_ownership=event_ownership,
            field_ownership=fields,
        ).binding_id,
        subject_ref="trip:abc",
        projection_role=role,
        calendar_ref=f"calendar:{role}",
        remote_event_ref="remote:event",
        event_ownership=event_ownership,
        field_ownership=fields,
        source_revision=2,
        projected_revision=1,
        remote_revision="etag:1",
        projected_field_hashes=projected or hashes(suffix="a"),
        status="ACTIVE",
    )


def remote(
    *, suffix: str = "a", start: int = 100, end: int = 200
) -> RemoteCalendarEvent:
    return RemoteCalendarEvent(
        remote_event_ref="remote:event",
        remote_revision="etag:2",
        status="ACTIVE",
        start_at=start,
        end_at=end,
        field_hashes=hashes(start=start, end=end, suffix=suffix),
    )


def test_missing_primary_event_creates_but_work_absence_requires_operator() -> None:
    primary = CalendarBinding.new(
        subject_ref="trip:abc",
        projection_role="travel_primary",
        calendar_ref="calendar:travel",
    )
    assert reconcile_calendar_event(desired(), primary, None).action == "CREATE"
    work = CalendarBinding.new(
        subject_ref="trip:abc",
        projection_role="work_absence",
        calendar_ref="calendar:work",
        event_ownership="SHARED",
        field_ownership={field: "SHARED" for field in hashes()},
    )
    result = reconcile_calendar_event(desired(role="work_absence"), work, None)
    assert result.action == "NEEDS_OPERATOR"
    assert "WORK_CALENDAR_BLOCK_REQUIRES_APPROVAL" in result.reason_codes


def test_safe_description_source_change_updates() -> None:
    projected = hashes(suffix="old")
    desired_hashes = dict(projected)
    desired_hashes["description"] = hash_calendar_field("description-new")
    binding = bound(projected=projected)
    desired_event = DesiredCalendarEvent(
        subject_ref="trip:abc",
        projection_role="travel_primary",
        source_revision=2,
        start_at=100,
        end_at=200,
        desired_status="ACTIVE",
        field_hashes=desired_hashes,
        private_payload_ref="private:travel_primary:new",
        confirmed=False,
    )
    result = reconcile_calendar_event(
        desired_event,
        binding,
        RemoteCalendarEvent(
            remote_event_ref="remote:event",
            remote_revision="etag:2",
            status="ACTIVE",
            start_at=100,
            end_at=200,
            field_hashes=projected,
        ),
    )
    assert result.action == "UPDATE"
    assert result.changed_fields == ("description",)


def test_manual_time_drift_on_confirmed_trip_requires_operator() -> None:
    binding = bound(projected=hashes())
    drifted = remote(start=120, end=220)
    result = reconcile_calendar_event(desired(confirmed=True), binding, drifted)
    assert result.action == "NEEDS_OPERATOR"
    assert "TIME_MANUAL_DRIFT" in result.reason_codes


def test_confirmed_trip_source_movement_requires_operator_even_if_remote_matches() -> None:
    binding = bound(projected=hashes(start=100, end=200))
    moved = desired(confirmed=True, start=120, end=220)
    remote_matches_new_source = remote(start=120, end=220)
    result = reconcile_calendar_event(moved, binding, remote_matches_new_source)
    assert result.action == "NEEDS_OPERATOR"
    assert (
        "CONFIRMED_TRIP_TIME_CHANGE_REQUIRES_APPROVAL"
        in result.reason_codes
    )


def test_invitation_and_attendee_source_changes_require_operator() -> None:
    binding = bound(projected=hashes(suffix="old"))
    invitation = desired(suffix="old", invitation_change=True)
    unchanged_remote = remote(suffix="old")
    explicit = reconcile_calendar_event(invitation, binding, unchanged_remote)
    assert explicit.action == "NEEDS_OPERATOR"
    assert "INVITATION_CHANGE_REQUIRES_APPROVAL" in explicit.reason_codes

    new_attendees = desired(suffix="new")
    remote_matches_new_source = remote(suffix="new")
    attendee_change = reconcile_calendar_event(
        new_attendees, binding, remote_matches_new_source
    )
    assert attendee_change.action == "NEEDS_OPERATOR"
    assert "ATTENDEE_CHANGE_REQUIRES_APPROVAL" in attendee_change.reason_codes


def test_external_deletion_and_confirmed_cancel_require_operator() -> None:
    binding = bound()
    assert reconcile_calendar_event(desired(), binding, None).action == "NEEDS_OPERATOR"
    result = reconcile_calendar_event(
        desired(status="CANCELLED", confirmed=True), binding, remote()
    )
    assert result.action == "NEEDS_OPERATOR"


def test_busy_intervals_merge_across_calendars_and_find_free_time() -> None:
    busy = (
        BusyInterval("cal:a", "event:a", 100, 200),
        BusyInterval("cal:b", "event:b", 150, 250),
        BusyInterval("cal:c", "event:c", 400, 500),
    )
    free = find_free_windows(
        busy, window_start=0, window_end=600, minimum_duration_seconds=100
    )
    assert [(item.start_at, item.end_at) for item in free] == [
        (0, 100),
        (250, 400),
        (500, 600),
    ]
    conflicts = find_conflicts(busy, start_at=225, end_at=425)
    assert [item.event_ref for item in conflicts] == ["event:b", "event:c"]


def test_aggregate_receipt_contains_counts_only() -> None:
    binding = CalendarBinding.new(
        subject_ref="trip:abc",
        projection_role="travel_primary",
        calendar_ref="calendar:travel",
    )
    proposal = reconcile_calendar_event(desired(), binding, None)
    receipt = aggregate_reconcile_receipt((proposal,))
    rendered = json.dumps(receipt)
    assert "trip:abc" not in rendered
    assert "calendar:travel" not in rendered
    assert receipt["actions"]["CREATE"] == 1


def test_external_owned_field_conflict_requires_operator() -> None:
    projected = hashes()
    ownership = {field: "SKELETON_OWNED" for field in projected}
    ownership["description"] = "EXTERNAL_OWNED"
    base = CalendarBinding.new(
        subject_ref="trip:abc",
        projection_role="travel_primary",
        calendar_ref="calendar:travel",
        event_ownership="SHARED",
        field_ownership=ownership,
    )
    binding = CalendarBinding(
        binding_id=base.binding_id,
        subject_ref=base.subject_ref,
        projection_role=base.projection_role,
        calendar_ref=base.calendar_ref,
        remote_event_ref="remote:event",
        event_ownership="SHARED",
        field_ownership=ownership,
        source_revision=2,
        projected_revision=1,
        remote_revision="etag:1",
        projected_field_hashes=projected,
        status="ACTIVE",
    )
    desired_hashes = dict(projected)
    desired_hashes["description"] = hash_calendar_field("new-description")
    desired_event = DesiredCalendarEvent(
        subject_ref="trip:abc",
        projection_role="travel_primary",
        source_revision=2,
        start_at=100,
        end_at=200,
        desired_status="ACTIVE",
        field_hashes=desired_hashes,
        private_payload_ref="private:new",
        confirmed=False,
    )
    result = reconcile_calendar_event(desired_event, binding, remote())
    assert result.action == "NEEDS_OPERATOR"
    assert "DESCRIPTION_OWNERSHIP_CONFLICT" in result.reason_codes


def test_completed_trip_archives_without_external_deletion() -> None:
    result = reconcile_calendar_event(
        desired(status="ARCHIVED", confirmed=True), bound(), remote()
    )
    assert result.action == "NOOP"
    assert result.reason_codes == ("ARCHIVED_NO_EXTERNAL_MUTATION",)
