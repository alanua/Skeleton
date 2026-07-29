from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Final


CALENDAR_BINDING_SCHEMA: Final = "skeleton.calendar_binding.v1"
CALENDAR_EVENT_SNAPSHOT_SCHEMA: Final = "skeleton.calendar_event_snapshot.v1"
DESIRED_CALENDAR_EVENT_SCHEMA: Final = "skeleton.desired_calendar_event.v1"
CALENDAR_MUTATION_PROPOSAL_SCHEMA: Final = "skeleton.calendar_mutation_proposal.v1"
CALENDAR_RECONCILE_RECEIPT_SCHEMA: Final = "skeleton.calendar_reconcile_receipt.v1"

PROJECTION_ROLES: Final = frozenset({"travel_primary", "family", "work_absence"})
EVENT_OWNERSHIP: Final = frozenset(
    {"SKELETON_OWNED", "EXTERNAL_OWNED", "SHARED", "USER_OVERRIDDEN"}
)
FIELD_NAMES: Final = (
    "time",
    "title",
    "description",
    "location",
    "attendees",
    "reminders",
)
FIELD_OWNERSHIP: Final = EVENT_OWNERSHIP
BINDING_STATUSES: Final = frozenset(
    {
        "ACTIVE",
        "CANCELLED",
        "ARCHIVED",
        "DELETED_EXTERNALLY",
        "DELETION_PENDING_APPROVAL",
    }
)
DESIRED_STATUSES: Final = frozenset({"ACTIVE", "CANCELLED", "ARCHIVED"})
REMOTE_STATUSES: Final = frozenset({"ACTIVE", "CANCELLED"})
PROPOSAL_ACTIONS: Final = frozenset(
    {"CREATE", "UPDATE", "CANCEL", "NOOP", "NEEDS_OPERATOR"}
)

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_REASON_CODES = 32


class CalendarPlanningValidationError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class CalendarBinding:
    binding_id: str
    subject_ref: str
    projection_role: str
    calendar_ref: str
    remote_event_ref: str | None
    event_ownership: str
    field_ownership: Mapping[str, str]
    source_revision: int
    projected_revision: int
    remote_revision: str | None
    projected_field_hashes: Mapping[str, str]
    status: str

    def __post_init__(self) -> None:
        _safe_token(self.binding_id, "binding_id")
        _safe_token(self.subject_ref, "subject_ref")
        _enum(self.projection_role, PROJECTION_ROLES, "projection_role")
        _safe_token(self.calendar_ref, "calendar_ref")
        if self.remote_event_ref is not None:
            _safe_token(self.remote_event_ref, "remote_event_ref")
        _enum(self.event_ownership, EVENT_OWNERSHIP, "event_ownership")
        object.__setattr__(
            self,
            "field_ownership",
            _field_ownership(self.field_ownership),
        )
        _non_negative_int(self.source_revision, "source_revision")
        _non_negative_int(self.projected_revision, "projected_revision")
        if self.projected_revision > self.source_revision:
            raise CalendarPlanningValidationError(
                "PROJECTED_REVISION_AHEAD",
                "projected_revision cannot exceed source_revision",
            )
        if self.remote_revision is not None:
            _safe_token(self.remote_revision, "remote_revision")
        object.__setattr__(
            self,
            "projected_field_hashes",
            _field_hashes(self.projected_field_hashes),
        )
        _enum(self.status, BINDING_STATUSES, "status")
        expected = stable_binding_id(self.subject_ref, self.projection_role)
        if self.binding_id != expected:
            raise CalendarPlanningValidationError(
                "BINDING_ID_MISMATCH", "binding_id is not deterministic"
            )

    @classmethod
    def new(
        cls,
        *,
        subject_ref: str,
        projection_role: str,
        calendar_ref: str,
        event_ownership: str = "SKELETON_OWNED",
        field_ownership: Mapping[str, str] | None = None,
    ) -> "CalendarBinding":
        ownership = field_ownership or {
            field: event_ownership for field in FIELD_NAMES
        }
        return cls(
            binding_id=stable_binding_id(subject_ref, projection_role),
            subject_ref=subject_ref,
            projection_role=projection_role,
            calendar_ref=calendar_ref,
            remote_event_ref=None,
            event_ownership=event_ownership,
            field_ownership=ownership,
            source_revision=0,
            projected_revision=0,
            remote_revision=None,
            projected_field_hashes={},
            status="ACTIVE",
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": CALENDAR_BINDING_SCHEMA,
            "binding_id": self.binding_id,
            "subject_ref": self.subject_ref,
            "projection_role": self.projection_role,
            "calendar_ref": self.calendar_ref,
            "remote_event_ref": self.remote_event_ref,
            "event_ownership": self.event_ownership,
            "field_ownership": dict(self.field_ownership),
            "source_revision": self.source_revision,
            "projected_revision": self.projected_revision,
            "remote_revision": self.remote_revision,
            "projected_field_hashes": dict(self.projected_field_hashes),
            "status": self.status,
        }


@dataclass(frozen=True)
class DesiredCalendarEvent:
    subject_ref: str
    projection_role: str
    source_revision: int
    start_at: int
    end_at: int
    desired_status: str
    field_hashes: Mapping[str, str]
    private_payload_ref: str
    confirmed: bool
    invitation_change: bool = False

    def __post_init__(self) -> None:
        _safe_token(self.subject_ref, "subject_ref")
        _enum(self.projection_role, PROJECTION_ROLES, "projection_role")
        _positive_int(self.source_revision, "source_revision")
        _time_range(self.start_at, self.end_at)
        _enum(self.desired_status, DESIRED_STATUSES, "desired_status")
        object.__setattr__(
            self, "field_hashes", _field_hashes(self.field_hashes, complete=True)
        )
        _safe_token(self.private_payload_ref, "private_payload_ref")
        if not isinstance(self.confirmed, bool):
            raise CalendarPlanningValidationError(
                "INVALID_CONFIRMED", "confirmed must be boolean"
            )
        if not isinstance(self.invitation_change, bool):
            raise CalendarPlanningValidationError(
                "INVALID_INVITATION_CHANGE", "invitation_change must be boolean"
            )
        expected_time_hash = hash_calendar_field(
            {"start_at": self.start_at, "end_at": self.end_at}
        )
        if self.field_hashes["time"] != expected_time_hash:
            raise CalendarPlanningValidationError(
                "TIME_HASH_MISMATCH", "time field hash does not match event range"
            )

    @property
    def event_key(self) -> str:
        return stable_event_key(self.subject_ref, self.projection_role)


@dataclass(frozen=True)
class RemoteCalendarEvent:
    remote_event_ref: str
    remote_revision: str
    status: str
    start_at: int
    end_at: int
    field_hashes: Mapping[str, str]

    def __post_init__(self) -> None:
        _safe_token(self.remote_event_ref, "remote_event_ref")
        _safe_token(self.remote_revision, "remote_revision")
        _enum(self.status, REMOTE_STATUSES, "status")
        _time_range(self.start_at, self.end_at)
        object.__setattr__(
            self, "field_hashes", _field_hashes(self.field_hashes, complete=True)
        )
        expected_time_hash = hash_calendar_field(
            {"start_at": self.start_at, "end_at": self.end_at}
        )
        if self.field_hashes["time"] != expected_time_hash:
            raise CalendarPlanningValidationError(
                "TIME_HASH_MISMATCH", "remote time hash does not match event range"
            )


@dataclass(frozen=True)
class BusyInterval:
    calendar_ref: str
    event_ref: str
    start_at: int
    end_at: int

    def __post_init__(self) -> None:
        _safe_token(self.calendar_ref, "calendar_ref")
        _safe_token(self.event_ref, "event_ref")
        _time_range(self.start_at, self.end_at)


@dataclass(frozen=True)
class CalendarMutationProposal:
    action: str
    reason_codes: tuple[str, ...]
    binding_id: str
    event_key: str
    projection_role: str
    expected_remote_revision: str | None
    source_revision: int
    changed_fields: tuple[str, ...]
    private_payload_ref: str | None
    operator_required: bool

    def __post_init__(self) -> None:
        _enum(self.action, PROPOSAL_ACTIONS, "action")
        if not self.reason_codes or len(self.reason_codes) > _MAX_REASON_CODES:
            raise CalendarPlanningValidationError(
                "INVALID_REASON_CODES", "reason_codes must be bounded and non-empty"
            )
        for reason in self.reason_codes:
            _safe_token(reason, "reason_code")
        _safe_token(self.binding_id, "binding_id")
        _safe_token(self.event_key, "event_key")
        _enum(self.projection_role, PROJECTION_ROLES, "projection_role")
        if self.expected_remote_revision is not None:
            _safe_token(self.expected_remote_revision, "expected_remote_revision")
        _positive_int(self.source_revision, "source_revision")
        if tuple(sorted(set(self.changed_fields))) != self.changed_fields:
            raise CalendarPlanningValidationError(
                "INVALID_CHANGED_FIELDS", "changed_fields must be sorted and unique"
            )
        for field in self.changed_fields:
            if field not in FIELD_NAMES:
                raise CalendarPlanningValidationError(
                    "INVALID_CHANGED_FIELD", "changed field is unknown"
                )
        if self.private_payload_ref is not None:
            _safe_token(self.private_payload_ref, "private_payload_ref")
        if not isinstance(self.operator_required, bool):
            raise CalendarPlanningValidationError(
                "INVALID_OPERATOR_REQUIRED", "operator_required must be boolean"
            )
        if (self.action == "NEEDS_OPERATOR") != self.operator_required:
            raise CalendarPlanningValidationError(
                "OPERATOR_ACTION_MISMATCH",
                "NEEDS_OPERATOR must match operator_required",
            )
        if self.action in {"CREATE", "UPDATE"} and self.private_payload_ref is None:
            raise CalendarPlanningValidationError(
                "PRIVATE_PAYLOAD_REF_REQUIRED",
                "create/update proposal requires private payload reference",
            )

    def deterministic_hash(self) -> str:
        encoded = json.dumps(
            self.private_mapping(),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def private_mapping(self) -> dict[str, Any]:
        return {
            "schema": CALENDAR_MUTATION_PROPOSAL_SCHEMA,
            "action": self.action,
            "reason_codes": list(self.reason_codes),
            "binding_id": self.binding_id,
            "event_key": self.event_key,
            "projection_role": self.projection_role,
            "expected_remote_revision": self.expected_remote_revision,
            "source_revision": self.source_revision,
            "changed_fields": list(self.changed_fields),
            "private_payload_ref": self.private_payload_ref,
            "operator_required": self.operator_required,
            "authority": {
                "proposal_only": True,
                "external_side_effects_executed": False,
            },
        }

    def public_receipt(self) -> dict[str, Any]:
        return {
            "schema": CALENDAR_RECONCILE_RECEIPT_SCHEMA,
            "status": "NEEDS_OPERATOR" if self.operator_required else "DONE",
            "action": self.action,
            "reason_codes": list(self.reason_codes),
            "changed_field_count": len(self.changed_fields),
            "operator_required": self.operator_required,
            "public_safe": True,
            "private_identifiers_included": False,
            "external_side_effects_executed": False,
        }


def stable_binding_id(subject_ref: str, projection_role: str) -> str:
    _safe_token(subject_ref, "subject_ref")
    _enum(projection_role, PROJECTION_ROLES, "projection_role")
    digest = hashlib.sha256(
        f"calendar-binding\n{subject_ref}\n{projection_role}".encode("utf-8")
    ).hexdigest()
    return f"calbind_{digest[:32]}"


def stable_event_key(subject_ref: str, projection_role: str) -> str:
    _safe_token(subject_ref, "subject_ref")
    _enum(projection_role, PROJECTION_ROLES, "projection_role")
    digest = hashlib.sha256(
        f"calendar-event\n{subject_ref}\n{projection_role}".encode("utf-8")
    ).hexdigest()
    return f"calevent_{digest[:32]}"


def hash_calendar_field(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _field_hashes(
    value: Mapping[str, str], *, complete: bool = False
) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise CalendarPlanningValidationError(
            "INVALID_FIELD_HASHES", "field hashes must be an object"
        )
    unknown = set(value) - set(FIELD_NAMES)
    if unknown:
        raise CalendarPlanningValidationError(
            "UNKNOWN_FIELD_HASH", f"unknown field hash: {sorted(unknown)[0]}"
        )
    if complete and set(value) != set(FIELD_NAMES):
        raise CalendarPlanningValidationError(
            "INCOMPLETE_FIELD_HASHES", "all calendar field hashes are required"
        )
    normalized: dict[str, str] = {}
    for field, digest in value.items():
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise CalendarPlanningValidationError(
                "INVALID_FIELD_HASH", f"{field} hash must be sha256"
            )
        normalized[field] = digest
    return MappingProxyType(dict(sorted(normalized.items())))


def _field_ownership(value: Mapping[str, str]) -> Mapping[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(FIELD_NAMES):
        raise CalendarPlanningValidationError(
            "INVALID_FIELD_OWNERSHIP", "ownership is required for every field"
        )
    normalized: dict[str, str] = {}
    for field in FIELD_NAMES:
        normalized[field] = _enum(
            value[field], FIELD_OWNERSHIP, f"{field}_ownership"
        )
    return MappingProxyType(normalized)


def _safe_token(value: object, field: str) -> str:
    if not isinstance(value, str) or _SAFE_TOKEN_RE.fullmatch(value) is None:
        raise CalendarPlanningValidationError(
            f"INVALID_{field.upper()}", f"{field} must be a bounded token"
        )
    return value


def _enum(value: object, allowed: frozenset[str], field: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise CalendarPlanningValidationError(
            f"INVALID_{field.upper()}", f"{field} is not allowlisted"
        )
    return value


def _non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CalendarPlanningValidationError(
            f"INVALID_{field.upper()}", f"{field} must be a non-negative integer"
        )
    return value


def _positive_int(value: object, field: str) -> int:
    result = _non_negative_int(value, field)
    if result == 0:
        raise CalendarPlanningValidationError(
            f"INVALID_{field.upper()}", f"{field} must be positive"
        )
    return result


def _time_range(start_at: object, end_at: object) -> None:
    start = _non_negative_int(start_at, "start_at")
    end = _non_negative_int(end_at, "end_at")
    if end <= start:
        raise CalendarPlanningValidationError(
            "INVALID_TIME_RANGE", "end_at must be after start_at"
        )
