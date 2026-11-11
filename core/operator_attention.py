from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from datetime import datetime
import re


ATTENTION_SCHEMA = "skeleton.operator_attention_request.v1"
RESOLUTION_SCHEMA = "skeleton.operator_attention_resolution.v1"

MAX_PUBLIC_SUMMARY_CHARS = 240
MAX_PRIVATE_INSTRUCTION_CHARS = 1000
MAX_TOKEN_CHARS = 128

_ATTENTION_ID_RE = re.compile(r"^attn:[a-z0-9][a-z0-9_.:-]{2,95}$")
_SUBJECT_REF_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,31}:[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{1,160}$")
_ACTOR_REFERENCE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9@._/-]{1,63}$")
_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


class AttentionKind(str, Enum):
    CAPTCHA = "captcha"
    OPERATOR_DECISION = "operator_decision"
    REVIEW = "review"


class AttentionPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class AttentionPrivacy(str, Enum):
    PUBLIC_SAFE = "public_safe"
    PRIVATE_RUNTIME = "private_runtime"


class AttentionChannel(str, Enum):
    LOCAL = "local"
    ANDROID = "android"
    TELEGRAM = "telegram"


class AttentionActionClass(str, Enum):
    HUMAN_CHALLENGE = "human_challenge"
    APPROVAL = "approval"
    REVIEW = "review"


class AttentionStatus(str, Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class AttentionResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class OperatorAttentionRequest:
    """Typed public-safe envelope for work that must pause for an operator."""

    attention_id: str
    kind: AttentionKind
    priority: AttentionPriority
    subject_ref: str
    created_at: str
    resume_token: str
    public_summary: str
    private_instruction: str
    privacy: AttentionPrivacy = AttentionPrivacy.PRIVATE_RUNTIME

    channel: AttentionChannel = AttentionChannel.LOCAL
    action_class: AttentionActionClass = AttentionActionClass.HUMAN_CHALLENGE
    idempotency_key: str | None = None
    expires_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "attention_id", _validated_match(self.attention_id, _ATTENTION_ID_RE, "attention_id"))
        object.__setattr__(self, "kind", _coerce_enum(self.kind, AttentionKind, "kind"))
        object.__setattr__(self, "priority", _coerce_enum(self.priority, AttentionPriority, "priority"))
        object.__setattr__(self, "subject_ref", _validated_match(self.subject_ref, _SUBJECT_REF_RE, "subject_ref"))
        object.__setattr__(self, "created_at", _validated_timestamp(self.created_at))
        object.__setattr__(self, "resume_token", _validated_match(self.resume_token, _TOKEN_RE, "resume_token"))
        object.__setattr__(self, "public_summary", _validated_text(self.public_summary, MAX_PUBLIC_SUMMARY_CHARS, "public_summary"))
        object.__setattr__(
            self,
            "private_instruction",
            _validated_text(self.private_instruction, MAX_PRIVATE_INSTRUCTION_CHARS, "private_instruction"),
        )
        object.__setattr__(self, "privacy", _coerce_enum(self.privacy, AttentionPrivacy, "privacy"))
        object.__setattr__(self, "channel", _coerce_enum(self.channel, AttentionChannel, "channel"))
        object.__setattr__(self, "action_class", _coerce_enum(self.action_class, AttentionActionClass, "action_class"))
        if self.idempotency_key is not None:
            _validated_match(self.idempotency_key, _TOKEN_RE, "idempotency_key")
        if self.expires_at is not None:
            _validated_timestamp(self.expires_at)
            if self.expires_at <= self.created_at:
                raise ValueError("expires_at must follow created_at.")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": ATTENTION_SCHEMA,
            "attention_id": self.attention_id,
            "kind": self.kind.value,
            "priority": self.priority.value,
            "subject_ref": self.subject_ref,
            "created_at": self.created_at,
            "resume_token": self.resume_token,
            "public_summary": self.public_summary,
            "private_instruction": self.private_instruction,
            "privacy": self.privacy.value,
            "channel": self.channel.value,
            "action_class": self.action_class.value,
            "idempotency_key": self.idempotency_key,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class OperatorAttentionResolution:
    """Typed resolution returned by the operator without executing side effects."""

    attention_id: str
    status: AttentionResolutionStatus
    actor_reference: str
    resolved_at: str
    resume_token: str
    public_note: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "attention_id", _validated_match(self.attention_id, _ATTENTION_ID_RE, "attention_id"))
        object.__setattr__(self, "status", _coerce_enum(self.status, AttentionResolutionStatus, "status"))
        object.__setattr__(self, "actor_reference", _validated_match(self.actor_reference, _ACTOR_REFERENCE_RE, "actor_reference"))
        object.__setattr__(self, "resolved_at", _validated_timestamp(self.resolved_at))
        object.__setattr__(self, "resume_token", _validated_match(self.resume_token, _TOKEN_RE, "resume_token"))
        object.__setattr__(self, "public_note", _validated_text(self.public_note, MAX_PUBLIC_SUMMARY_CHARS, "public_note"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": RESOLUTION_SCHEMA,
            "attention_id": self.attention_id,
            "status": self.status.value,
            "actor_reference": self.actor_reference,
            "resolved_at": self.resolved_at,
            "resume_token": self.resume_token,
            "public_note": self.public_note,
        }


def _coerce_enum(value: object, enum_type: type[Enum], field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)  # type: ignore[call-arg]
    except ValueError as exc:
        raise ValueError(f"{field_name} is not supported.") from exc


def _validated_match(value: object, pattern: re.Pattern[str], field_name: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{field_name} is malformed.")
    return value


def _validated_timestamp(value: object) -> str:
    if not isinstance(value, str) or _UTC_TIMESTAMP_RE.fullmatch(value) is None:
        raise ValueError("timestamp must be a UTC RFC3339 second timestamp.")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError("timestamp is not a valid calendar date.") from exc
    return value


def _validated_text(value: object, max_chars: int, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text.")
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError(f"{field_name} must not be empty.")
    if len(normalized) > max_chars:
        raise ValueError(f"{field_name} must be at most {max_chars} characters.")
    return normalized


@dataclass(frozen=True)
class OperatorAttentionReceipt:
    """Allowlisted public output: never includes caller text or runtime references."""

    attention_id: str
    status: AttentionStatus

    def __post_init__(self) -> None:
        _validated_match(self.attention_id, _ATTENTION_ID_RE, "attention_id")
        object.__setattr__(self, "status", _coerce_enum(self.status, AttentionStatus, "status"))

    def to_dict(self) -> dict[str, str]:
        return {"attention_id": self.attention_id, "status": self.status.value}


class OperatorAttentionLifecycle:
    """In-memory contract model; channels are labels and never send notifications."""

    def __init__(self) -> None:
        self._requests: dict[str, OperatorAttentionRequest] = {}
        self._statuses: dict[str, AttentionStatus] = {}
        self._keys: dict[str, str] = {}
        self._resolutions: dict[str, OperatorAttentionResolution] = {}

    def submit(self, request: OperatorAttentionRequest) -> OperatorAttentionReceipt:
        if not isinstance(request, OperatorAttentionRequest):
            raise ValueError("request must be an OperatorAttentionRequest.")
        key = request.idempotency_key or request.attention_id
        previous = self._keys.get(key, request.attention_id)
        if previous != request.attention_id:
            raise ValueError("IDEMPOTENCY_CONFLICT")
        if request.attention_id in self._requests:
            if self._requests[request.attention_id] != request:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return self.receipt(request.attention_id)
        self._requests[request.attention_id] = request
        self._keys[key] = request.attention_id
        self._statuses[request.attention_id] = AttentionStatus.PENDING
        return self.receipt(request.attention_id)

    def receipt(self, attention_id: str) -> OperatorAttentionReceipt:
        if attention_id not in self._requests:
            raise ValueError("ATTENTION_NOT_FOUND")
        return OperatorAttentionReceipt(attention_id, self._statuses[attention_id])

    def advance(self, attention_id: str, status: AttentionStatus, *, now: str) -> OperatorAttentionReceipt:
        self.expire(attention_id, now=now)
        status = _coerce_enum(status, AttentionStatus, "status")
        current = self._statuses[attention_id]
        if current == status:
            return self.receipt(attention_id)
        allowed = {
            AttentionStatus.PENDING: {AttentionStatus.DELIVERED, AttentionStatus.CANCELLED},
            AttentionStatus.DELIVERED: {AttentionStatus.ACKNOWLEDGED, AttentionStatus.CANCELLED},
            AttentionStatus.ACKNOWLEDGED: {AttentionStatus.CANCELLED},
        }
        if status not in allowed.get(current, set()):
            raise ValueError("INVALID_ATTENTION_TRANSITION")
        self._statuses[attention_id] = status
        return self.receipt(attention_id)

    def expire(self, attention_id: str, *, now: str) -> OperatorAttentionReceipt:
        self.receipt(attention_id)
        _validated_timestamp(now)
        request = self._requests[attention_id]
        if now < request.created_at:
            raise ValueError("timestamp precedes request.")
        if (request.expires_at is not None and now >= request.expires_at
                and self._statuses[attention_id] in {
                    AttentionStatus.PENDING, AttentionStatus.DELIVERED, AttentionStatus.ACKNOWLEDGED}):
            self._statuses[attention_id] = AttentionStatus.EXPIRED
        return self.receipt(attention_id)

    def resolve(self, resolution: OperatorAttentionResolution) -> OperatorAttentionReceipt:
        if not isinstance(resolution, OperatorAttentionResolution):
            raise ValueError("resolution must be an OperatorAttentionResolution.")
        attention_id = resolution.attention_id
        self.receipt(attention_id)
        request = self._requests[attention_id]
        if resolution.resume_token != request.resume_token:
            raise ValueError("ATTENTION_TOKEN_MISMATCH")
        self.expire(attention_id, now=resolution.resolved_at)
        if attention_id in self._resolutions:
            if self._resolutions[attention_id] != resolution:
                raise ValueError("RESOLUTION_CONFLICT")
            return self.receipt(attention_id)
        current = self._statuses[attention_id]
        if current not in {AttentionStatus.PENDING, AttentionStatus.DELIVERED, AttentionStatus.ACKNOWLEDGED}:
            raise ValueError("ATTENTION_ALREADY_TERMINAL")
        if resolution.status is AttentionResolutionStatus.EXPIRED:
            raise ValueError("EXPIRY_REQUIRES_DEADLINE")
        self._statuses[attention_id] = AttentionStatus(resolution.status.value)
        self._resolutions[attention_id] = resolution
        return self.receipt(attention_id)
