from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from core.calendar_planning_models import (
    CALENDAR_RECONCILE_RECEIPT_SCHEMA,
    FIELD_NAMES,
    BusyInterval,
    CalendarBinding,
    CalendarMutationProposal,
    CalendarPlanningValidationError,
    DesiredCalendarEvent,
    RemoteCalendarEvent,
)


_PROTECTED_FIELDS = frozenset({"time", "attendees"})


@dataclass(frozen=True)
class FreeWindow:
    start_at: int
    end_at: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.start_at, bool)
            or not isinstance(self.start_at, int)
            or self.start_at < 0
            or isinstance(self.end_at, bool)
            or not isinstance(self.end_at, int)
            or self.end_at <= self.start_at
        ):
            raise CalendarPlanningValidationError(
                "INVALID_FREE_WINDOW", "free window is invalid"
            )

    @property
    def duration_seconds(self) -> int:
        return self.end_at - self.start_at


def normalize_busy_intervals(
    intervals: Iterable[BusyInterval],
    *,
    window_start: int,
    window_end: int,
) -> tuple[FreeWindow, ...]:
    _window(window_start, window_end)
    clipped: list[tuple[int, int]] = []
    for interval in intervals:
        start = max(window_start, interval.start_at)
        end = min(window_end, interval.end_at)
        if start < end:
            clipped.append((start, end))
    if not clipped:
        return ()
    clipped.sort()
    merged: list[FreeWindow] = []
    current_start, current_end = clipped[0]
    for start, end in clipped[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            merged.append(FreeWindow(current_start, current_end))
            current_start, current_end = start, end
    merged.append(FreeWindow(current_start, current_end))
    return tuple(merged)


def find_free_windows(
    intervals: Iterable[BusyInterval],
    *,
    window_start: int,
    window_end: int,
    minimum_duration_seconds: int,
) -> tuple[FreeWindow, ...]:
    _window(window_start, window_end)
    if (
        isinstance(minimum_duration_seconds, bool)
        or not isinstance(minimum_duration_seconds, int)
        or minimum_duration_seconds <= 0
    ):
        raise CalendarPlanningValidationError(
            "INVALID_MINIMUM_DURATION", "minimum duration must be positive"
        )
    busy = normalize_busy_intervals(
        intervals, window_start=window_start, window_end=window_end
    )
    cursor = window_start
    free: list[FreeWindow] = []
    for interval in busy:
        if interval.start_at - cursor >= minimum_duration_seconds:
            free.append(FreeWindow(cursor, interval.start_at))
        cursor = max(cursor, interval.end_at)
    if window_end - cursor >= minimum_duration_seconds:
        free.append(FreeWindow(cursor, window_end))
    return tuple(free)


def find_conflicts(
    intervals: Iterable[BusyInterval],
    *,
    start_at: int,
    end_at: int,
    ignored_event_refs: frozenset[str] = frozenset(),
) -> tuple[BusyInterval, ...]:
    _window(start_at, end_at)
    if not isinstance(ignored_event_refs, frozenset) or any(
        not isinstance(value, str) for value in ignored_event_refs
    ):
        raise CalendarPlanningValidationError(
            "INVALID_IGNORED_EVENTS", "ignored_event_refs must be a string frozenset"
        )
    conflicts = [
        interval
        for interval in intervals
        if interval.event_ref not in ignored_event_refs
        and interval.start_at < end_at
        and start_at < interval.end_at
    ]
    conflicts.sort(
        key=lambda item: (
            item.start_at,
            item.end_at,
            item.calendar_ref,
            item.event_ref,
        )
    )
    return tuple(conflicts)


def reconcile_calendar_event(
    desired: DesiredCalendarEvent,
    binding: CalendarBinding,
    remote: RemoteCalendarEvent | None,
) -> CalendarMutationProposal:
    _require_matching_identity(desired, binding)

    if binding.status == "ARCHIVED" or desired.desired_status == "ARCHIVED":
        return _proposal(
            "NOOP",
            ("ARCHIVED_NO_EXTERNAL_MUTATION",),
            desired,
            binding,
            remote,
            (),
            payload=False,
        )

    if remote is None:
        if binding.remote_event_ref is not None:
            if desired.desired_status in {"CANCELLED", "ARCHIVED"}:
                return _proposal(
                    "NOOP",
                    ("REMOTE_ALREADY_ABSENT",),
                    desired,
                    binding,
                    remote,
                    (),
                    payload=False,
                )
            return _proposal(
                "NEEDS_OPERATOR",
                ("REMOTE_EVENT_DELETED_EXTERNALLY",),
                desired,
                binding,
                remote,
                ("time",),
                payload=False,
            )
        if desired.desired_status != "ACTIVE":
            return _proposal(
                "NOOP",
                ("NO_REMOTE_EVENT_REQUIRED",),
                desired,
                binding,
                remote,
                (),
                payload=False,
            )
        if desired.projection_role == "work_absence":
            return _proposal(
                "NEEDS_OPERATOR",
                ("WORK_CALENDAR_BLOCK_REQUIRES_APPROVAL",),
                desired,
                binding,
                remote,
                tuple(FIELD_NAMES),
                payload=False,
            )
        if desired.invitation_change:
            return _proposal(
                "NEEDS_OPERATOR",
                ("INVITATION_CHANGE_REQUIRES_APPROVAL",),
                desired,
                binding,
                remote,
                ("attendees",),
                payload=False,
            )
        return _proposal(
            "CREATE",
            ("REMOTE_EVENT_MISSING",),
            desired,
            binding,
            remote,
            tuple(FIELD_NAMES),
            payload=True,
        )

    if binding.remote_event_ref is None:
        return _proposal(
            "NEEDS_OPERATOR",
            ("UNBOUND_REMOTE_EVENT",),
            desired,
            binding,
            remote,
            tuple(FIELD_NAMES),
            payload=False,
        )
    if remote.remote_event_ref != binding.remote_event_ref:
        raise CalendarPlanningValidationError(
            "REMOTE_EVENT_REF_MISMATCH",
            "remote event does not match binding",
        )

    if desired.desired_status == "CANCELLED":
        if desired.confirmed or binding.event_ownership != "SKELETON_OWNED":
            return _proposal(
                "NEEDS_OPERATOR",
                ("CONFIRMED_EVENT_CANCELLATION_REQUIRES_APPROVAL",),
                desired,
                binding,
                remote,
                (),
                payload=False,
            )
        if remote.status == "CANCELLED":
            return _proposal(
                "NOOP",
                ("REMOTE_ALREADY_CANCELLED",),
                desired,
                binding,
                remote,
                (),
                payload=False,
            )
        return _proposal(
            "CANCEL",
            ("CANDIDATE_EVENT_CANCELLED",),
            desired,
            binding,
            remote,
            (),
            payload=False,
        )

    if remote.status == "CANCELLED":
        return _proposal(
            "NEEDS_OPERATOR",
            ("REMOTE_EVENT_CANCELLED_EXTERNALLY",),
            desired,
            binding,
            remote,
            ("time",),
            payload=False,
        )

    changed: list[str] = []
    operator_reasons: list[str] = []
    safe_reasons: list[str] = []
    for field in FIELD_NAMES:
        desired_hash = desired.field_hashes[field]
        remote_hash = remote.field_hashes[field]
        projected_hash = binding.projected_field_hashes.get(field)
        if desired_hash == remote_hash:
            continue
        ownership = binding.field_ownership[field]
        remote_drift = projected_hash is not None and remote_hash != projected_hash
        source_changed = projected_hash is None or desired_hash != projected_hash

        if ownership in {"EXTERNAL_OWNED", "USER_OVERRIDDEN"}:
            operator_reasons.append(f"{field.upper()}_OWNERSHIP_CONFLICT")
            changed.append(field)
            continue
        if ownership == "SHARED" and (
            remote_drift or field in _PROTECTED_FIELDS
        ):
            operator_reasons.append(f"{field.upper()}_SHARED_DRIFT")
            changed.append(field)
            continue
        if remote_drift and field in _PROTECTED_FIELDS:
            operator_reasons.append(f"{field.upper()}_MANUAL_DRIFT")
            changed.append(field)
            continue
        if field == "attendees" or desired.invitation_change:
            operator_reasons.append("INVITATION_CHANGE_REQUIRES_APPROVAL")
            changed.append(field)
            continue
        if source_changed or remote_drift:
            changed.append(field)
            safe_reasons.append(f"{field.upper()}_UPDATE")

    changed_tuple = tuple(sorted(set(changed)))
    if operator_reasons:
        return _proposal(
            "NEEDS_OPERATOR",
            tuple(sorted(set(operator_reasons))),
            desired,
            binding,
            remote,
            changed_tuple,
            payload=False,
        )
    if not changed_tuple:
        reason = (
            "REMOTE_MATCHES_SOURCE"
            if binding.projected_revision < desired.source_revision
            else "PROJECTION_UP_TO_DATE"
        )
        return _proposal(
            "NOOP",
            (reason,),
            desired,
            binding,
            remote,
            (),
            payload=False,
        )
    return _proposal(
        "UPDATE",
        tuple(sorted(set(safe_reasons))) or ("SAFE_FIELD_UPDATE",),
        desired,
        binding,
        remote,
        changed_tuple,
        payload=True,
    )


def aggregate_reconcile_receipt(
    proposals: Sequence[CalendarMutationProposal],
) -> dict[str, Any]:
    if not isinstance(proposals, Sequence):
        raise CalendarPlanningValidationError(
            "INVALID_PROPOSALS", "proposals must be a sequence"
        )
    actions = Counter(proposal.action for proposal in proposals)
    reasons = Counter(
        reason for proposal in proposals for reason in proposal.reason_codes
    )
    return {
        "schema": CALENDAR_RECONCILE_RECEIPT_SCHEMA,
        "status": "NEEDS_OPERATOR" if actions["NEEDS_OPERATOR"] else "DONE",
        "proposal_count": len(proposals),
        "actions": {
            action: actions.get(action, 0)
            for action in (
                "CREATE",
                "UPDATE",
                "CANCEL",
                "NOOP",
                "NEEDS_OPERATOR",
            )
        },
        "reason_counts": dict(sorted(reasons.items())),
        "changed_field_count": sum(
            len(proposal.changed_fields) for proposal in proposals
        ),
        "operator_required_count": actions["NEEDS_OPERATOR"],
        "public_safe": True,
        "private_identifiers_included": False,
        "external_side_effects_executed": False,
    }


def _proposal(
    action: str,
    reasons: tuple[str, ...],
    desired: DesiredCalendarEvent,
    binding: CalendarBinding,
    remote: RemoteCalendarEvent | None,
    changed_fields: tuple[str, ...],
    *,
    payload: bool,
) -> CalendarMutationProposal:
    return CalendarMutationProposal(
        action=action,
        reason_codes=tuple(sorted(set(reasons))),
        binding_id=binding.binding_id,
        event_key=desired.event_key,
        projection_role=desired.projection_role,
        expected_remote_revision=(
            remote.remote_revision if remote is not None else None
        ),
        source_revision=desired.source_revision,
        changed_fields=tuple(sorted(set(changed_fields))),
        private_payload_ref=desired.private_payload_ref if payload else None,
        operator_required=action == "NEEDS_OPERATOR",
    )


def _require_matching_identity(
    desired: DesiredCalendarEvent, binding: CalendarBinding
) -> None:
    if (
        desired.subject_ref != binding.subject_ref
        or desired.projection_role != binding.projection_role
    ):
        raise CalendarPlanningValidationError(
            "CALENDAR_BINDING_SUBJECT_MISMATCH",
            "desired event and binding identities differ",
        )
    if desired.source_revision < binding.source_revision:
        raise CalendarPlanningValidationError(
            "SOURCE_REVISION_REGRESSION",
            "desired source revision is older than binding source revision",
        )


def _window(start_at: object, end_at: object) -> None:
    if (
        isinstance(start_at, bool)
        or not isinstance(start_at, int)
        or start_at < 0
        or isinstance(end_at, bool)
        or not isinstance(end_at, int)
        or end_at <= start_at
    ):
        raise CalendarPlanningValidationError(
            "INVALID_TIME_WINDOW", "time window is invalid"
        )
