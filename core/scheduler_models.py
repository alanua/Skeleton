from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SCHEDULE_SCHEMA: Final = "skeleton.schedule.v1"
SCHEDULE_RECORD_SCHEMA: Final = "skeleton.schedule_record.v1"
EXECUTION_PROPOSAL_SCHEMA: Final = "skeleton.scheduler_execution_proposal.v1"
OCCURRENCE_RECEIPT_SCHEMA: Final = "skeleton.scheduler_occurrence_receipt.v1"
TICK_RECEIPT_SCHEMA: Final = "skeleton.scheduler_tick_receipt.v1"

TRIGGER_KINDS: Final = frozenset({"cron", "once"})
ROUTE_TYPES: Final = frozenset({"notify", "skill", "workflow", "loop", "runner"})
APPROVAL_POLICIES: Final = frozenset(
    {"notify_only", "auto_run_low_risk", "require_operator_each_occurrence"}
)
OVERLAP_POLICIES: Final = frozenset({"skip", "queue_one", "needs_operator"})
MISFIRE_POLICIES: Final = frozenset({"run_once", "skip", "needs_operator"})
OCCURRENCE_STATES: Final = frozenset(
    {
        "pending",
        "running",
        "done",
        "failed",
        "waiting_dependency",
        "needs_operator",
        "skipped",
    }
)

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_PAYLOAD_BYTES = 64 * 1024
_MAX_PAYLOAD_DEPTH = 12
_MAX_PAYLOAD_ITEMS = 256
_MAX_STRING_LENGTH = 4096


class SchedulerValidationError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ScheduleSpec:
    schedule_id: str
    trigger_kind: str
    cron_expression: str | None
    once_at: int | None
    timezone: str
    route_type: str
    route_id: str
    approval_policy: str
    overlap_policy: str
    misfire_policy: str
    payload: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ScheduleSpec":
        if not isinstance(value, Mapping):
            raise SchedulerValidationError("INVALID_SCHEDULE", "schedule must be an object")
        required = {
            "schema", "schedule_id", "trigger_kind", "cron_expression", "once_at",
            "timezone", "route_type", "route_id", "approval_policy",
            "overlap_policy", "misfire_policy", "payload",
        }
        keys = set(value)
        unknown = sorted(keys - required)
        missing = sorted(required - keys)
        if unknown:
            raise SchedulerValidationError(
                "UNKNOWN_SCHEDULE_FIELD", f"unknown schedule field: {unknown[0]}"
            )
        if missing:
            raise SchedulerValidationError(
                "MISSING_SCHEDULE_FIELD", f"missing schedule field: {missing[0]}"
            )
        if value.get("schema") != SCHEDULE_SCHEMA:
            raise SchedulerValidationError("INVALID_SCHEDULE_SCHEMA", "invalid schedule schema")

        schedule_id = _safe_token(value.get("schedule_id"), "schedule_id")
        trigger_kind = _enum(value.get("trigger_kind"), TRIGGER_KINDS, "trigger_kind")
        timezone_name = _timezone_name(value.get("timezone"))
        route_type = _enum(value.get("route_type"), ROUTE_TYPES, "route_type")
        route_id = _safe_token(value.get("route_id"), "route_id")
        approval_policy = _enum(
            value.get("approval_policy"), APPROVAL_POLICIES, "approval_policy"
        )
        overlap_policy = _enum(
            value.get("overlap_policy"), OVERLAP_POLICIES, "overlap_policy"
        )
        misfire_policy = _enum(
            value.get("misfire_policy"), MISFIRE_POLICIES, "misfire_policy"
        )
        payload = _payload(value.get("payload"))

        cron_expression: str | None
        once_at: int | None
        if trigger_kind == "cron":
            cron_expression = _cron_expression(value.get("cron_expression"))
            if value.get("once_at") is not None:
                raise SchedulerValidationError(
                    "INVALID_TRIGGER_FIELDS", "cron schedule must not define once_at"
                )
            once_at = None
        else:
            if value.get("cron_expression") is not None:
                raise SchedulerValidationError(
                    "INVALID_TRIGGER_FIELDS", "once schedule must not define cron_expression"
                )
            once_at = _non_negative_int(value.get("once_at"), "once_at")
            cron_expression = None

        return cls(
            schedule_id=schedule_id,
            trigger_kind=trigger_kind,
            cron_expression=cron_expression,
            once_at=once_at,
            timezone=timezone_name,
            route_type=route_type,
            route_id=route_id,
            approval_policy=approval_policy,
            overlap_policy=overlap_policy,
            misfire_policy=misfire_policy,
            payload=payload,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": SCHEDULE_SCHEMA,
            "schedule_id": self.schedule_id,
            "trigger_kind": self.trigger_kind,
            "cron_expression": self.cron_expression,
            "once_at": self.once_at,
            "timezone": self.timezone,
            "route_type": self.route_type,
            "route_id": self.route_id,
            "approval_policy": self.approval_policy,
            "overlap_policy": self.overlap_policy,
            "misfire_policy": self.misfire_policy,
            "payload": thaw_json(self.payload),
        }

    def deterministic_hash(self) -> str:
        encoded = json.dumps(
            self.to_mapping(), ensure_ascii=True, allow_nan=False,
            separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class StoredSchedule:
    spec: ScheduleSpec
    version: int
    enabled: bool
    created_at: int
    last_evaluated_at: int | None

    def to_mapping(self, *, include_payload: bool = True) -> dict[str, Any]:
        payload = self.spec.to_mapping()
        if not include_payload:
            payload.pop("payload", None)
        return {
            "schema": SCHEDULE_RECORD_SCHEMA,
            "version": self.version,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "last_evaluated_at": self.last_evaluated_at,
            **{key: value for key, value in payload.items() if key != "schema"},
        }


@dataclass(frozen=True)
class OccurrenceRecord:
    occurrence_id: str
    schedule_id: str
    schedule_version: int
    scheduled_for: int
    state: str
    reason: str
    proposal: Mapping[str, Any]
    created_at: int
    updated_at: int
    started_at: int | None
    attempt: int = 0
    idempotency_key: str | None = None
    parent_occurrence_id: str | None = None
    parent_receipt_id: str | None = None

    def public_receipt(self) -> dict[str, Any]:
        return {
            "schema": OCCURRENCE_RECEIPT_SCHEMA,
            "occurrence_id": self.occurrence_id,
            "schedule_id": self.schedule_id,
            "schedule_version": self.schedule_version,
            "scheduled_for": self.scheduled_for,
            "state": self.state,
            "reason": self.reason,
            "attempt": self.attempt,
            "idempotency_key": self.idempotency_key,
            "parent_occurrence_id": self.parent_occurrence_id,
            "parent_receipt_id": self.parent_receipt_id,
            "public_safe": True,
            "payload_included": False,
        }


def stable_occurrence_id(schedule_id: str, version: int, scheduled_for: int) -> str:
    schedule_id = _safe_token(schedule_id, "schedule_id")
    version = _positive_int(version, "version")
    scheduled_for = _non_negative_int(scheduled_for, "scheduled_for")
    digest = hashlib.sha256(
        f"{schedule_id}\n{version}\n{scheduled_for}".encode("utf-8")
    ).hexdigest()
    return f"occ_{digest[:32]}"


def stable_followup_occurrence_id(parent_occurrence_id: str, step_id: str) -> str:
    parent_occurrence_id = _safe_token(parent_occurrence_id, "parent_occurrence_id")
    step_id = _safe_token(step_id, "step_id")
    digest = hashlib.sha256(f"{parent_occurrence_id}\n{step_id}".encode("utf-8")).hexdigest()
    return f"occ_{digest[:32]}"


def build_execution_proposal(
    schedule: StoredSchedule, *, occurrence_id: str, scheduled_for: int
) -> dict[str, Any]:
    _safe_token(occurrence_id, "occurrence_id")
    _non_negative_int(scheduled_for, "scheduled_for")
    return {
        "schema": EXECUTION_PROPOSAL_SCHEMA,
        "occurrence_id": occurrence_id,
        "schedule_id": schedule.spec.schedule_id,
        "schedule_version": schedule.version,
        "scheduled_for": scheduled_for,
        "route_type": schedule.spec.route_type,
        "route_id": schedule.spec.route_id,
        "approval_policy": schedule.spec.approval_policy,
        "payload": thaw_json(schedule.spec.payload),
        "authority": {
            "proposal_only": True,
            "external_side_effects_executed": False,
            "runner_enqueued": False,
            "loop_started": False,
        },
    }


def iter_due_times(
    spec: ScheduleSpec,
    *,
    after_exclusive: int,
    until_inclusive: int,
    limit: int = 256,
) -> tuple[int, ...]:
    after_exclusive = _non_negative_int(after_exclusive, "after_exclusive")
    until_inclusive = _non_negative_int(until_inclusive, "until_inclusive")
    limit = _positive_int(limit, "limit")
    if until_inclusive <= after_exclusive:
        return ()
    if spec.trigger_kind == "once":
        assert spec.once_at is not None
        return (spec.once_at,) if after_exclusive < spec.once_at <= until_inclusive else ()

    assert spec.cron_expression is not None
    fields = _parse_cron(spec.cron_expression)
    zone = ZoneInfo(spec.timezone)
    first = ((after_exclusive // 60) + 1) * 60
    due: list[int] = []
    candidate = first
    while candidate <= until_inclusive and len(due) < limit:
        local = datetime.fromtimestamp(candidate, tz=timezone.utc).astimezone(zone)
        if local.second == 0 and _cron_matches(fields, local):
            due.append(candidate)
        candidate += 60
    return tuple(due)


def thaw_json(value: object) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(child) for child in value]
    return value


def _safe_token(value: object, field: str) -> str:
    if not isinstance(value, str) or _SAFE_TOKEN_RE.fullmatch(value) is None:
        raise SchedulerValidationError(
            f"INVALID_{field.upper()}", f"{field} must be a bounded token"
        )
    return value


def _enum(value: object, allowed: frozenset[str], field: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise SchedulerValidationError(
            f"INVALID_{field.upper()}", f"{field} is not allowlisted"
        )
    return value


def _non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SchedulerValidationError(
            f"INVALID_{field.upper()}", f"{field} must be a non-negative integer"
        )
    return value


def _positive_int(value: object, field: str) -> int:
    normalized = _non_negative_int(value, field)
    if normalized == 0:
        raise SchedulerValidationError(
            f"INVALID_{field.upper()}", f"{field} must be a positive integer"
        )
    return normalized


def _timezone_name(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise SchedulerValidationError("INVALID_TIMEZONE", "timezone is invalid")
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise SchedulerValidationError("INVALID_TIMEZONE", "timezone is unknown") from exc
    return value


def _payload(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SchedulerValidationError("INVALID_PAYLOAD", "payload must be an object")
    frozen = _freeze_json(value, depth=0, path="payload")
    assert isinstance(frozen, Mapping)
    encoded = json.dumps(
        thaw_json(frozen), ensure_ascii=True, allow_nan=False,
        separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > _MAX_PAYLOAD_BYTES:
        raise SchedulerValidationError("PAYLOAD_TOO_LARGE", "payload exceeds size limit")
    return frozen


def _freeze_json(value: object, *, depth: int, path: str) -> Any:
    if depth > _MAX_PAYLOAD_DEPTH:
        raise SchedulerValidationError("PAYLOAD_TOO_DEEP", f"{path} is too deep")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SchedulerValidationError("INVALID_PAYLOAD", f"{path} is not finite")
        return value
    if isinstance(value, str):
        if len(value) > _MAX_STRING_LENGTH:
            raise SchedulerValidationError("PAYLOAD_STRING_TOO_LONG", f"{path} is too long")
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_PAYLOAD_ITEMS:
            raise SchedulerValidationError("PAYLOAD_TOO_LARGE", f"{path} has too many fields")
        if any(not isinstance(key, str) or not key or len(key) > 128 for key in value):
            raise SchedulerValidationError("INVALID_PAYLOAD_KEY", f"{path} has an invalid key")
        return MappingProxyType({
            key: _freeze_json(value[key], depth=depth + 1, path=f"{path}.{key}")
            for key in sorted(value)
        })
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > _MAX_PAYLOAD_ITEMS:
            raise SchedulerValidationError("PAYLOAD_TOO_LARGE", f"{path} has too many items")
        return tuple(
            _freeze_json(child, depth=depth + 1, path=f"{path}[{index}]")
            for index, child in enumerate(value)
        )
    raise SchedulerValidationError("INVALID_PAYLOAD", f"{path} contains a non-JSON value")


def _cron_expression(value: object) -> str:
    if not isinstance(value, str) or len(value) > 128:
        raise SchedulerValidationError("INVALID_CRON", "cron expression is invalid")
    normalized = " ".join(value.split())
    _parse_cron(normalized)
    return normalized


@dataclass(frozen=True)
class _CronField:
    values: frozenset[int]
    wildcard: bool


def _parse_cron(expression: str) -> tuple[_CronField, ...]:
    parts = expression.split()
    if len(parts) != 5:
        raise SchedulerValidationError("INVALID_CRON", "cron must have five fields")
    return (
        _parse_cron_field(parts[0], 0, 59, "minute"),
        _parse_cron_field(parts[1], 0, 23, "hour"),
        _parse_cron_field(parts[2], 1, 31, "day_of_month"),
        _parse_cron_field(parts[3], 1, 12, "month"),
        _parse_cron_field(parts[4], 0, 7, "day_of_week", normalize_weekday=True),
    )


def _parse_cron_field(
    text: str,
    minimum: int,
    maximum: int,
    field: str,
    *,
    normalize_weekday: bool = False,
) -> _CronField:
    if not text:
        raise SchedulerValidationError("INVALID_CRON", f"empty {field}")
    wildcard = text == "*" or text.startswith("*/")
    values: set[int] = set()
    for part in text.split(","):
        if not part:
            raise SchedulerValidationError("INVALID_CRON", f"invalid {field}")
        base, slash, step_text = part.partition("/")
        step = 1
        if slash:
            try:
                step = int(step_text)
            except ValueError as exc:
                raise SchedulerValidationError("INVALID_CRON", f"invalid {field} step") from exc
            if step <= 0:
                raise SchedulerValidationError("INVALID_CRON", f"invalid {field} step")
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            start_text, end_text = base.split("-", 1)
            start = _cron_int(start_text, minimum, maximum, field)
            end = _cron_int(end_text, minimum, maximum, field)
            if end < start:
                raise SchedulerValidationError("INVALID_CRON", f"invalid {field} range")
        else:
            start = end = _cron_int(base, minimum, maximum, field)
            if slash:
                end = maximum
        for item in range(start, end + 1, step):
            values.add(0 if normalize_weekday and item == 7 else item)
    if not values:
        raise SchedulerValidationError("INVALID_CRON", f"empty {field}")
    return _CronField(frozenset(values), wildcard)


def _cron_int(text: str, minimum: int, maximum: int, field: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise SchedulerValidationError("INVALID_CRON", f"invalid {field}") from exc
    if value < minimum or value > maximum:
        raise SchedulerValidationError("INVALID_CRON", f"{field} out of range")
    return value


def _cron_matches(fields: tuple[_CronField, ...], local: datetime) -> bool:
    minute, hour, day, month, weekday = fields
    if local.minute not in minute.values or local.hour not in hour.values:
        return False
    if local.month not in month.values:
        return False
    day_match = local.day in day.values
    weekday_match = ((local.weekday() + 1) % 7) in weekday.values
    if day.wildcard and weekday.wildcard:
        return True
    if day.wildcard:
        return weekday_match
    if weekday.wildcard:
        return day_match
    return day_match or weekday_match
