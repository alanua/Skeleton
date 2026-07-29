from __future__ import annotations

import json
from pathlib import Path

from core.calendar_planning_models import hash_calendar_field
from core.scheduler_models import thaw_json
from core.travel_calendar_planning import (
    TravelPlanCalendarInput,
    build_desired_calendar_events,
    build_initial_bindings,
    build_travel_schedule_bundle,
    schedule_bundle_public_receipt,
)


def role_hashes(
    role: str, start: int = 2_000_000, end: int = 2_100_000
) -> dict[str, str]:
    return {
        "time": hash_calendar_field({"start_at": start, "end_at": end}),
        "title": hash_calendar_field(f"title:{role}"),
        "description": hash_calendar_field(f"description:{role}"),
        "location": hash_calendar_field(f"location:{role}"),
        "attendees": hash_calendar_field([]),
        "reminders": hash_calendar_field([86400]),
    }


def plan(lifecycle: str = "PLANNED") -> TravelPlanCalendarInput:
    roles = ("family", "travel_primary", "work_absence")
    return TravelPlanCalendarInput(
        trip_ref="trip:opaque-123",
        lifecycle=lifecycle,
        source_revision=3,
        start_at=2_000_000,
        end_at=2_100_000,
        timezone="Europe/Berlin",
        confirmed=lifecycle != "CANDIDATE",
        projection_roles=roles,
        projection_field_hashes={role: role_hashes(role) for role in roles},
        projection_payload_refs={
            role: f"private:{role}:payload" for role in roles
        },
    )


def test_multi_calendar_bindings_have_role_specific_ownership() -> None:
    value = plan()
    bindings = build_initial_bindings(
        value,
        {
            "family": "calendar:family",
            "travel_primary": "calendar:travel",
            "work_absence": "calendar:work",
        },
    )
    by_role = {item.projection_role: item for item in bindings}
    assert by_role["travel_primary"].event_ownership == "SKELETON_OWNED"
    assert by_role["family"].field_ownership["attendees"] == "SHARED"
    assert (
        by_role["work_absence"].field_ownership["location"]
        == "EXTERNAL_OWNED"
    )
    desired = build_desired_calendar_events(value)
    assert {item.projection_role for item in desired} == set(
        value.projection_roles
    )


def test_candidate_and_planned_schedule_bundles_use_typed_workflows() -> None:
    candidate = build_travel_schedule_bundle(plan("CANDIDATE"), now=1_000_000)
    candidate_routes = {item.route_id for item in candidate}
    assert candidate_routes == {
        "travel.calendar_reconcile",
        "travel.price_check_due",
        "travel.trip_review_due",
    }
    planned = build_travel_schedule_bundle(plan("PLANNED"), now=1_000_000)
    planned_routes = {item.route_id for item in planned}
    assert "travel.trip_preparation_t30" not in planned_routes
    assert "travel.trip_preparation_t7" in planned_routes
    assert "travel.departure_preflight" in planned_routes
    assert all(item.route_type == "workflow" for item in planned)


def test_completed_and_cancelled_disable_future_schedules() -> None:
    assert build_travel_schedule_bundle(plan("COMPLETED"), now=1_000_000) == ()
    assert build_travel_schedule_bundle(plan("CANCELLED"), now=1_000_000) == ()


def test_schedule_payloads_have_only_opaque_domain_refs() -> None:
    value = plan("CANDIDATE")
    schedules = build_travel_schedule_bundle(value, now=1_000_000)
    rendered = json.dumps([thaw_json(item.payload) for item in schedules])
    assert "calendar:travel" not in rendered
    assert "private:" not in rendered
    assert "google" not in rendered.casefold()
    assert "trip:opaque-123" in rendered
    assert all(
        item.payload["authority"]["calendar_provider_mutation"] is False
        for item in schedules
    )
    receipt = schedule_bundle_public_receipt(value, schedules)
    public = json.dumps(receipt)
    assert "trip:opaque-123" not in public
    assert "private:" not in public
    assert receipt["provider_identifiers_included"] is False


def test_schedule_ids_are_stable_and_unique() -> None:
    value = plan("PLANNED")
    first = build_travel_schedule_bundle(value, now=1_000_000)
    second = build_travel_schedule_bundle(value, now=1_000_000)
    assert [item.schedule_id for item in first] == [
        item.schedule_id for item in second
    ]
    assert len({item.schedule_id for item in first}) == len(first)


def test_source_modules_have_no_provider_or_side_effect_imports() -> None:
    forbidden = (
        "googleapiclient",
        "google.oauth",
        "requests",
        "urllib.request",
        "http.client",
        "sqlite3",
        "subprocess",
        "os.system",
    )
    for name in (
        "core/calendar_planning_models.py",
        "core/calendar_planning.py",
        "core/travel_calendar_planning.py",
    ):
        source = Path(name).read_text(encoding="utf-8").casefold()
        assert not any(token in source for token in forbidden)
