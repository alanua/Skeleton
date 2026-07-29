from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import re
from types import MappingProxyType
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.calendar_planning_models import (
    FIELD_NAMES,
    PROJECTION_ROLES,
    CalendarBinding,
    CalendarPlanningValidationError,
    DesiredCalendarEvent,
)
from core.scheduler_models import ScheduleSpec


TRAVEL_PLAN_SCHEDULE_BUNDLE_SCHEMA = "skeleton.travel_plan_schedule_bundle.v1"
TRAVEL_LIFECYCLES = frozenset(
    {"CANDIDATE", "PLANNED", "IN_TRIP", "COMPLETED", "CANCELLED"}
)
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")


@dataclass(frozen=True)
class TravelPlanCalendarInput:
    trip_ref: str
    lifecycle: str
    source_revision: int
    start_at: int
    end_at: int
    timezone: str
    confirmed: bool
    projection_roles: tuple[str, ...]
    projection_field_hashes: Mapping[str, Mapping[str, str]]
    projection_payload_refs: Mapping[str, str]
    invitation_roles: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        _safe_token(self.trip_ref, "trip_ref")
        if self.lifecycle not in TRAVEL_LIFECYCLES:
            raise CalendarPlanningValidationError(
                "INVALID_TRAVEL_LIFECYCLE", "travel lifecycle is invalid"
            )
        if (
            isinstance(self.source_revision, bool)
            or not isinstance(self.source_revision, int)
            or self.source_revision <= 0
        ):
            raise CalendarPlanningValidationError(
                "INVALID_SOURCE_REVISION", "source_revision must be positive"
            )
        if (
            isinstance(self.start_at, bool)
            or not isinstance(self.start_at, int)
            or self.start_at < 0
            or isinstance(self.end_at, bool)
            or not isinstance(self.end_at, int)
            or self.end_at <= self.start_at
        ):
            raise CalendarPlanningValidationError(
                "INVALID_TRIP_RANGE", "trip time range is invalid"
            )
        if not isinstance(self.confirmed, bool):
            raise CalendarPlanningValidationError(
                "INVALID_CONFIRMED", "confirmed must be boolean"
            )
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, TypeError) as exc:
            raise CalendarPlanningValidationError(
                "INVALID_TIMEZONE", "travel timezone is invalid"
            ) from exc
        roles = tuple(sorted(set(self.projection_roles)))
        if roles != self.projection_roles or "travel_primary" not in roles:
            raise CalendarPlanningValidationError(
                "INVALID_PROJECTION_ROLES",
                "projection roles must be sorted, unique and include travel_primary",
            )
        if any(role not in PROJECTION_ROLES for role in roles):
            raise CalendarPlanningValidationError(
                "INVALID_PROJECTION_ROLE", "projection role is not allowlisted"
            )
        if set(self.projection_field_hashes) != set(roles):
            raise CalendarPlanningValidationError(
                "PROJECTION_HASH_ROLE_MISMATCH",
                "field hashes are required for every projection role",
            )
        if set(self.projection_payload_refs) != set(roles):
            raise CalendarPlanningValidationError(
                "PROJECTION_PAYLOAD_ROLE_MISMATCH",
                "payload references are required for every projection role",
            )
        normalized_hashes: dict[str, Mapping[str, str]] = {}
        normalized_refs: dict[str, str] = {}
        for role in roles:
            hashes = self.projection_field_hashes[role]
            if not isinstance(hashes, Mapping) or set(hashes) != set(FIELD_NAMES):
                raise CalendarPlanningValidationError(
                    "INCOMPLETE_PROJECTION_FIELD_HASHES",
                    "all field hashes are required for every projection",
                )
            normalized_hashes[role] = MappingProxyType(dict(hashes))
            normalized_refs[role] = _safe_token(
                self.projection_payload_refs[role], "private_payload_ref"
            )
        object.__setattr__(
            self,
            "projection_field_hashes",
            MappingProxyType(normalized_hashes),
        )
        object.__setattr__(
            self,
            "projection_payload_refs",
            MappingProxyType(normalized_refs),
        )
        if not isinstance(self.invitation_roles, frozenset) or any(
            role not in roles for role in self.invitation_roles
        ):
            raise CalendarPlanningValidationError(
                "INVALID_INVITATION_ROLES", "invitation roles are invalid"
            )


def build_initial_bindings(
    plan: TravelPlanCalendarInput,
    calendar_targets: Mapping[str, str],
) -> tuple[CalendarBinding, ...]:
    if not isinstance(calendar_targets, Mapping) or set(calendar_targets) != set(
        plan.projection_roles
    ):
        raise CalendarPlanningValidationError(
            "CALENDAR_TARGET_ROLE_MISMATCH",
            "one opaque calendar target is required per projection role",
        )
    bindings: list[CalendarBinding] = []
    for role in plan.projection_roles:
        calendar_ref = _safe_token(calendar_targets[role], "calendar_ref")
        event_ownership, field_ownership = _ownership_policy(role)
        bindings.append(
            CalendarBinding.new(
                subject_ref=plan.trip_ref,
                projection_role=role,
                calendar_ref=calendar_ref,
                event_ownership=event_ownership,
                field_ownership=field_ownership,
            )
        )
    return tuple(bindings)


def build_desired_calendar_events(
    plan: TravelPlanCalendarInput,
) -> tuple[DesiredCalendarEvent, ...]:
    status = (
        "CANCELLED"
        if plan.lifecycle == "CANCELLED"
        else "ARCHIVED"
        if plan.lifecycle == "COMPLETED"
        else "ACTIVE"
    )
    return tuple(
        DesiredCalendarEvent(
            subject_ref=plan.trip_ref,
            projection_role=role,
            source_revision=plan.source_revision,
            start_at=plan.start_at,
            end_at=plan.end_at,
            desired_status=status,
            field_hashes=plan.projection_field_hashes[role],
            private_payload_ref=plan.projection_payload_refs[role],
            confirmed=plan.confirmed,
            invitation_change=role in plan.invitation_roles,
        )
        for role in plan.projection_roles
    )


def build_travel_schedule_bundle(
    plan: TravelPlanCalendarInput,
    *,
    now: int,
) -> tuple[ScheduleSpec, ...]:
    if isinstance(now, bool) or not isinstance(now, int) or now < 0:
        raise CalendarPlanningValidationError("INVALID_NOW", "now must be non-negative")
    if plan.lifecycle in {"COMPLETED", "CANCELLED"}:
        return ()

    schedules: list[ScheduleSpec] = []
    schedules.append(
        _cron_schedule(
            plan,
            operation="travel.calendar_reconcile",
            expression="15 4 * * *",
            approval_policy="auto_run_low_risk",
        )
    )

    if plan.lifecycle == "CANDIDATE":
        schedules.extend(
            (
                _cron_schedule(
                    plan,
                    operation="travel.trip_review_due",
                    expression="0 18 * * 0",
                    approval_policy="auto_run_low_risk",
                ),
                _cron_schedule(
                    plan,
                    operation="travel.price_check_due",
                    expression="0 7 * * 3",
                    approval_policy="auto_run_low_risk",
                ),
            )
        )
    elif plan.lifecycle == "PLANNED":
        schedules.append(
            _cron_schedule(
                plan,
                operation="travel.price_check_due",
                expression="0 7 * * 2",
                approval_policy="auto_run_low_risk",
            )
        )
        for operation, offset_seconds, approval_policy in (
            ("travel.trip_preparation_t30", 30 * 86400, "notify_only"),
            ("travel.trip_preparation_t7", 7 * 86400, "notify_only"),
            ("travel.itinerary_refresh", 3 * 86400, "auto_run_low_risk"),
            ("travel.departure_preflight", 1 * 86400, "notify_only"),
        ):
            due = plan.start_at - offset_seconds
            if due > now:
                schedules.append(
                    _once_schedule(
                        plan,
                        operation=operation,
                        once_at=due,
                        approval_policy=approval_policy,
                    )
                )
    elif plan.lifecycle == "IN_TRIP":
        schedules.append(
            _cron_schedule(
                plan,
                operation="travel.itinerary_refresh",
                expression="0 7 * * *",
                approval_policy="auto_run_low_risk",
            )
        )

    schedules.sort(key=lambda item: item.schedule_id)
    return tuple(schedules)


def schedule_bundle_public_receipt(
    plan: TravelPlanCalendarInput,
    schedules: tuple[ScheduleSpec, ...],
) -> dict[str, Any]:
    counts = Counter(schedule.route_id for schedule in schedules)
    return {
        "schema": TRAVEL_PLAN_SCHEDULE_BUNDLE_SCHEMA,
        "status": "DONE",
        "lifecycle": plan.lifecycle,
        "schedule_count": len(schedules),
        "operation_counts": dict(sorted(counts.items())),
        "projection_count": len(plan.projection_roles),
        "public_safe": True,
        "private_identifiers_included": False,
        "provider_identifiers_included": False,
        "external_side_effects_executed": False,
    }


def _cron_schedule(
    plan: TravelPlanCalendarInput,
    *,
    operation: str,
    expression: str,
    approval_policy: str,
) -> ScheduleSpec:
    return ScheduleSpec.from_mapping(
        {
            "schema": "skeleton.schedule.v1",
            "schedule_id": _schedule_id(plan.trip_ref, operation),
            "trigger_kind": "cron",
            "cron_expression": expression,
            "once_at": None,
            "timezone": plan.timezone,
            "route_type": "workflow",
            "route_id": operation,
            "approval_policy": approval_policy,
            "overlap_policy": "queue_one",
            "misfire_policy": "run_once",
            "payload": _schedule_payload(plan, operation),
        }
    )


def _once_schedule(
    plan: TravelPlanCalendarInput,
    *,
    operation: str,
    once_at: int,
    approval_policy: str,
) -> ScheduleSpec:
    return ScheduleSpec.from_mapping(
        {
            "schema": "skeleton.schedule.v1",
            "schedule_id": _schedule_id(plan.trip_ref, operation),
            "trigger_kind": "once",
            "cron_expression": None,
            "once_at": once_at,
            "timezone": plan.timezone,
            "route_type": "workflow",
            "route_id": operation,
            "approval_policy": approval_policy,
            "overlap_policy": "skip",
            "misfire_policy": "needs_operator",
            "payload": _schedule_payload(plan, operation),
        }
    )


def _schedule_payload(
    plan: TravelPlanCalendarInput, operation: str
) -> dict[str, Any]:
    return {
        "schema": "skeleton.travel_schedule_payload.v1",
        "trip_ref": plan.trip_ref,
        "operation": operation,
        "source_revision": plan.source_revision,
        "projection_roles": list(plan.projection_roles),
        "authority": {
            "typed_workflow_only": True,
            "calendar_provider_mutation": False,
            "booking_or_payment": False,
        },
    }


def _schedule_id(trip_ref: str, operation: str) -> str:
    digest = hashlib.sha256(
        f"travel-schedule\n{trip_ref}\n{operation}".encode("utf-8")
    ).hexdigest()
    suffix = operation.split(".")[-1].replace("_", "-")[:40]
    return f"travel-{suffix}-{digest[:24]}"


def _ownership_policy(role: str) -> tuple[str, Mapping[str, str]]:
    if role == "travel_primary":
        return "SKELETON_OWNED", {
            field: "SKELETON_OWNED" for field in FIELD_NAMES
        }
    if role == "family":
        return "SHARED", {
            "time": "SHARED",
            "title": "SKELETON_OWNED",
            "description": "SKELETON_OWNED",
            "location": "SKELETON_OWNED",
            "attendees": "SHARED",
            "reminders": "SKELETON_OWNED",
        }
    if role == "work_absence":
        return "SHARED", {
            "time": "SHARED",
            "title": "SKELETON_OWNED",
            "description": "SKELETON_OWNED",
            "location": "EXTERNAL_OWNED",
            "attendees": "SHARED",
            "reminders": "EXTERNAL_OWNED",
        }
    raise CalendarPlanningValidationError(
        "INVALID_PROJECTION_ROLE", "projection role is not allowlisted"
    )


def _safe_token(value: object, field: str) -> str:
    if not isinstance(value, str) or _SAFE_TOKEN_RE.fullmatch(value) is None:
        raise CalendarPlanningValidationError(
            f"INVALID_{field.upper()}", f"{field} must be a bounded token"
        )
    return value
