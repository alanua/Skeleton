from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
import re

ATTENTION_SCHEMA = "skeleton.operator_attention_request.v1"
RESOLUTION_SCHEMA = "skeleton.operator_attention_resolution.v1"
_ATTENTION_ID_RE = re.compile(r"^attn:[a-z0-9][a-z0-9_.:-]{2,95}$")
_SESSION_REF_RE = re.compile(r"^session:[a-f0-9]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
_ACTOR_REFERENCE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9@._/-]{1,63}$")
_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")

class AttentionReason(str, Enum):
    CAPTCHA = "captcha"
    MFA = "mfa"
    OAUTH_CONSENT = "oauth_consent"
    ACCOUNT_SELECTION = "account_selection"
    SITE_CONSENT = "site_consent"
    MANUAL_REVIEW = "manual_review"
    UNKNOWN_INTERACTIVE_CHALLENGE = "unknown_interactive_challenge"

class AttentionStatus(str, Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    OPENED = "opened"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"

class AttentionChannel(str, Enum):
    ANDROID_HOME = "android_home"
    TELEGRAM = "telegram"

class WebActionClass(str, Enum):
    READ_ONLY = "read_only"
    PRIVATE_SESSION_MUTATION = "private_session_mutation"
    EXTERNAL_MUTATION = "external_mutation"
    HIGH_IMPACT = "high_impact"

class AttentionResolutionStatus(str, Enum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"

TERMINAL = {AttentionStatus.COMPLETED, AttentionStatus.EXPIRED, AttentionStatus.CANCELLED}
TRANSITIONS = {
    AttentionStatus.PENDING: {AttentionStatus.DELIVERED, AttentionStatus.CANCELLED},
    AttentionStatus.DELIVERED: {AttentionStatus.OPENED, AttentionStatus.CANCELLED},
    AttentionStatus.OPENED: {AttentionStatus.IN_PROGRESS, AttentionStatus.CANCELLED},
    AttentionStatus.IN_PROGRESS: {AttentionStatus.COMPLETED, AttentionStatus.CANCELLED},
}

def _display(reason):
    return ("Operator attention required", f"Private session requires {reason.value.replace('_', ' ')}.")

@dataclass(frozen=True)
class OperatorAttentionRequest:
    """Private runtime record. Public output must use public_receipt()."""
    attention_id: str
    reason: AttentionReason
    source_capability: str
    source_operation: str
    browser_session_ref: str
    created_at: str
    expires_at: str
    idempotency_key: str
    resume_token: str
    display_title: str
    display_message: str
    delivery_channels: tuple[AttentionChannel, ...] = tuple(AttentionChannel)
    status: AttentionStatus = AttentionStatus.PENDING

    def __post_init__(self):
        for field, pattern in (("attention_id", _ATTENTION_ID_RE), ("source_capability", _IDENTIFIER_RE),
                               ("source_operation", _IDENTIFIER_RE), ("browser_session_ref", _SESSION_REF_RE),
                               ("idempotency_key", _TOKEN_RE), ("resume_token", _TOKEN_RE)):
            _validated_match(getattr(self, field), pattern, field)
        for field, enum in (("reason", AttentionReason), ("status", AttentionStatus)):
            object.__setattr__(self, field, _coerce_enum(getattr(self, field), enum, field))
        _validated_timestamp(self.created_at)
        _validated_timestamp(self.expires_at)
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must follow created_at")
        channels = tuple(_coerce_enum(c, AttentionChannel, "delivery_channels") for c in self.delivery_channels)
        if not 1 <= len(channels) <= 2 or len(set(channels)) != len(channels):
            raise ValueError("delivery_channels must be a bounded unique subset")
        object.__setattr__(self, "delivery_channels", channels)
        # Fixed templates prevent URL, page text and credentials entering public output.
        title, message = _display(self.reason)
        if (self.display_title, self.display_message) != (title, message):
            raise ValueError("display fields must use sanitized reason templates")

    @property
    def request_id(self):
        return self.attention_id

    def public_receipt(self):
        return {"attention_id": self.attention_id, "reason": self.reason.value,
                "status": self.status.value, "source_capability": self.source_capability,
                "source_operation": self.source_operation, "created_at": self.created_at,
                "expires_at": self.expires_at, "delivery_channels": [c.value for c in self.delivery_channels],
                "display_title": self.display_title, "display_message": self.display_message,
                "reason_code": self.reason.name + "_OPERATOR_HANDOFF_REQUIRED"}

    def to_private_dict(self):
        return dict(self.public_receipt(), schema=ATTENTION_SCHEMA,
                    browser_session_ref=self.browser_session_ref, idempotency_key=self.idempotency_key,
                    resume_token=self.resume_token)

@dataclass(frozen=True)
class OperatorAttentionResolution:
    attention_id: str
    status: AttentionResolutionStatus
    actor_reference: str
    resolved_at: str
    resume_token: str

    def __post_init__(self):
        _validated_match(self.attention_id, _ATTENTION_ID_RE, "attention_id")
        _validated_match(self.actor_reference, _ACTOR_REFERENCE_RE, "actor_reference")
        _validated_match(self.resume_token, _TOKEN_RE, "resume_token")
        _validated_timestamp(self.resolved_at)
        object.__setattr__(self, "status", _coerce_enum(self.status, AttentionResolutionStatus, "status"))

    def to_private_dict(self):
        return dict(schema=RESOLUTION_SCHEMA, attention_id=self.attention_id, status=self.status.value,
                    actor_reference=self.actor_reference, resolved_at=self.resolved_at, resume_token=self.resume_token)

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



class OperatorAttentionLifecycle:
    """Canonical in-memory state; projections never send notifications."""
    def __init__(self):
        self._requests = {}
        self._keys = {}
        self._resolutions = {}

    def submit(self, request):
        if not isinstance(request, OperatorAttentionRequest) or request.status is not AttentionStatus.PENDING:
            raise ValueError("INVALID_REQUEST")
        previous = self._keys.get(request.idempotency_key)
        if previous is not None:
            canonical = self._requests[previous]
            if replace(canonical, attention_id=request.attention_id, status=request.status) != request:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return canonical
        if request.attention_id in self._requests:
            raise ValueError("IDEMPOTENCY_CONFLICT")
        self._requests[request.attention_id] = request
        self._keys[request.idempotency_key] = request.attention_id
        return request

    def request(self, attention_id):
        if attention_id not in self._requests:
            raise ValueError("ATTENTION_NOT_FOUND")
        return self._requests[attention_id]

    def receipt(self, attention_id):
        return self.request(attention_id).public_receipt()

    def projections(self, attention_id):
        request = self.request(attention_id)
        return {c.value: request.public_receipt() for c in request.delivery_channels}

    def expire(self, attention_id, *, now):
        request = self.request(attention_id)
        _validated_timestamp(now)
        if now < request.created_at:
            raise ValueError("timestamp precedes request")
        if now >= request.expires_at and request.status not in TERMINAL:
            self._requests[attention_id] = replace(request, status=AttentionStatus.EXPIRED)
        return self.receipt(attention_id)

    def advance(self, attention_id, status, *, now):
        self.expire(attention_id, now=now)
        status = _coerce_enum(status, AttentionStatus, "status")
        request = self.request(attention_id)
        # Completion requires the private resolution token.
        if status is AttentionStatus.COMPLETED or status not in TRANSITIONS.get(request.status, set()):
            raise ValueError("INVALID_ATTENTION_TRANSITION")
        self._requests[attention_id] = replace(request, status=status)
        return self.receipt(attention_id)

    def resolve(self, resolution):
        if not isinstance(resolution, OperatorAttentionResolution):
            raise ValueError("INVALID_RESOLUTION")
        request = self.request(resolution.attention_id)
        if request.resume_token != resolution.resume_token:
            raise ValueError("ATTENTION_TOKEN_MISMATCH")
        self.expire(request.attention_id, now=resolution.resolved_at)
        if request.attention_id in self._resolutions:
            if self._resolutions[request.attention_id] != resolution:
                raise ValueError("RESOLUTION_CONFLICT")
            return self.receipt(request.attention_id)
        request = self.request(request.attention_id)
        target = AttentionStatus(resolution.status.value)
        if target not in TRANSITIONS.get(request.status, set()):
            raise ValueError("INVALID_ATTENTION_TRANSITION")
        self._requests[request.attention_id] = replace(request, status=target)
        self._resolutions[request.attention_id] = resolution
        return self.receipt(request.attention_id)
