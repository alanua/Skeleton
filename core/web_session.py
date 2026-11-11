from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import json
import re
from urllib.parse import urlparse

from core.operator_attention import (
    AttentionKind,
    OperatorAttentionLifecycle,
    AttentionPriority,
    OperatorAttentionRequest,
    OperatorAttentionResolution,
)


WEB_SESSION_SCHEMA = "skeleton.web_session_state.v1"

_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
_SESSION_ID_RE = re.compile(r"^sess:[a-z0-9][a-z0-9_.:-]{2,95}$")
_CHALLENGE_ID_RE = re.compile(r"^captcha:[a-z0-9][a-z0-9_.:-]{2,95}$")
_ORIGIN_RE = re.compile(r"^https://[A-Za-z0-9.-]{1,120}(?::[0-9]{2,5})?$")
_URL_RE = re.compile(r"^https://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{1,220}$")


class WebSessionStatus(str, Enum):
    OPEN = "open"
    WAITING_FOR_OPERATOR = "waiting_for_operator"
    RESUMED = "resumed"
    CLOSED = "closed"


@dataclass(frozen=True)
class WebSessionBinding:
    """Provider-neutral identity for one exact private browser/session runtime."""

    provider_id: str
    session_id: str
    session_epoch: int
    origin: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _validated_match(self.provider_id, _ID_RE, "provider_id"))
        object.__setattr__(self, "session_id", _validated_match(self.session_id, _SESSION_ID_RE, "session_id"))
        object.__setattr__(self, "session_epoch", _validated_positive_int(self.session_epoch, "session_epoch"))
        object.__setattr__(self, "origin", _validated_match(self.origin, _ORIGIN_RE, "origin"))

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "session_id": self.session_id,
            "session_epoch": self.session_epoch,
            "origin": self.origin,
        }


@dataclass(frozen=True)
class WebCaptchaChallenge:
    challenge_id: str
    binding: WebSessionBinding
    page_url: str
    reason_code: str = "CAPTCHA_OPERATOR_HANDOFF_REQUIRED"

    def __post_init__(self) -> None:
        object.__setattr__(self, "challenge_id", _validated_match(self.challenge_id, _CHALLENGE_ID_RE, "challenge_id"))
        if not isinstance(self.binding, WebSessionBinding):
            raise ValueError("binding must be a WebSessionBinding.")
        object.__setattr__(self, "page_url", _validated_match(self.page_url, _URL_RE, "page_url"))
        object.__setattr__(self, "reason_code", _validated_reason_code(self.reason_code))
        if _origin_for_url(self.page_url) != self.binding.origin:
            raise ValueError("challenge origin differs from binding.")

    def resume_token(self) -> str:
        return _stable_token(
            {
                "challenge_id": self.challenge_id,
                "binding": self.binding.to_dict(),
                "page_url": self.page_url,
                "reason_code": self.reason_code,
            }
        )

    def to_attention_request(self, *, created_at: str, expires_at: str | None = None) -> OperatorAttentionRequest:
        return OperatorAttentionRequest(
            attention_id=f"attn:{self.challenge_id.removeprefix('captcha:')}",
            kind=AttentionKind.CAPTCHA,
            priority=AttentionPriority.HIGH,
            subject_ref=f"web-session:{self.binding.session_id}",
            created_at=created_at,
            expires_at=expires_at,
            idempotency_key=self.resume_token(),
            resume_token=self.resume_token(),
            public_summary="CAPTCHA requires operator attention for a private web session.",
            private_instruction=(
                "Solve the CAPTCHA in the existing private browser session only; "
                "do not create a new session, export cookies, or send credentials."
            ),
        )


@dataclass(frozen=True)
class WebSessionState:
    binding: WebSessionBinding
    current_url: str
    status: WebSessionStatus
    pending_challenge: WebCaptchaChallenge | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.binding, WebSessionBinding):
            raise ValueError("binding must be a WebSessionBinding.")
        object.__setattr__(self, "current_url", _validated_match(self.current_url, _URL_RE, "current_url"))
        object.__setattr__(self, "status", _coerce_status(self.status))
        if self.pending_challenge is not None and not isinstance(self.pending_challenge, WebCaptchaChallenge):
            raise ValueError("pending_challenge must be a WebCaptchaChallenge.")
        if _origin_for_url(self.current_url) != self.binding.origin:
            raise ValueError("current URL origin differs from binding.")
        if (self.status is WebSessionStatus.WAITING_FOR_OPERATOR) != (self.pending_challenge is not None):
            raise ValueError("waiting state requires exactly one pending challenge.")
        if self.pending_challenge is not None and (
            self.pending_challenge.binding != self.binding or self.pending_challenge.page_url != self.current_url
        ):
            raise ValueError("pending challenge differs from exact session.")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": WEB_SESSION_SCHEMA,
            "binding": self.binding.to_dict(),
            "current_url": self.current_url,
            "status": self.status.value,
            "pending_challenge": None
            if self.pending_challenge is None
            else {
                "challenge_id": self.pending_challenge.challenge_id,
                "page_url": self.pending_challenge.page_url,
                "reason_code": self.pending_challenge.reason_code,
                "resume_token": self.pending_challenge.resume_token(),
            },
        }


class SyntheticWebSessionAdapter:
    """Deterministic fake adapter for contract tests; it never opens a browser."""

    provider_id = "synthetic-web"

    def __init__(self) -> None:
        self._sessions: dict[str, WebSessionState] = {}
        self.attention = OperatorAttentionLifecycle()
        self._attention_bindings: dict[str, WebSessionBinding] = {}

    def open_session(self, *, session_id: str, url: str) -> WebSessionState:
        if session_id in self._sessions:
            raise ValueError("WEB_SESSION_ALREADY_EXISTS")
        origin = _origin_for_url(url)
        binding = WebSessionBinding(
            provider_id=self.provider_id,
            session_id=session_id,
            session_epoch=1,
            origin=origin,
        )
        state = WebSessionState(binding=binding, current_url=url, status=WebSessionStatus.OPEN)
        self._sessions[session_id] = state
        return state

    def require_captcha(
        self, *, session_id: str, challenge_id: str,
        created_at: str = "2026-10-03T00:00:00Z", expires_at: str | None = None,
    ) -> OperatorAttentionRequest:
        state = self._existing(session_id)
        challenge = WebCaptchaChallenge(challenge_id=challenge_id, binding=state.binding, page_url=state.current_url)
        if state.status is WebSessionStatus.CLOSED:
            raise ValueError("WEB_SESSION_CLOSED")
        if state.pending_challenge is not None and state.pending_challenge != challenge:
            raise ValueError("WEB_SESSION_ALREADY_WAITING")
        request = challenge.to_attention_request(created_at=created_at, expires_at=expires_at)
        self.attention.submit(request)
        self._attention_bindings[request.attention_id] = state.binding
        self._sessions[session_id] = replace(
            state,
            status=WebSessionStatus.WAITING_FOR_OPERATOR,
            pending_challenge=challenge,
        )
        return request

    def resume_after_attention(self, *, binding: WebSessionBinding, resolution: OperatorAttentionResolution) -> WebSessionState:
        state = self._existing(binding.session_id)
        if state.binding != binding or self._attention_bindings.get(resolution.attention_id) != binding:
            raise ValueError("RESUME_REJECTED_SESSION_BINDING_MISMATCH")
        if state.status is not WebSessionStatus.WAITING_FOR_OPERATOR or state.pending_challenge is None:
            raise ValueError("RESUME_REJECTED_NO_PENDING_CHALLENGE")
        if resolution.attention_id != f"attn:{state.pending_challenge.challenge_id.removeprefix('captcha:')}":
            raise ValueError("RESUME_REJECTED_ATTENTION_MISMATCH")
        if resolution.resume_token != state.pending_challenge.resume_token():
            raise ValueError("RESUME_REJECTED_TOKEN_MISMATCH")
        if resolution.status.value != "resolved":
            raise ValueError("RESUME_REJECTED_UNRESOLVED_ATTENTION")
        self.attention.resolve(resolution)
        resumed = replace(state, status=WebSessionStatus.RESUMED, pending_challenge=None)
        self._sessions[binding.session_id] = resumed
        return resumed

    def state(self, session_id: str) -> WebSessionState:
        return self._existing(session_id)

    def _existing(self, session_id: str) -> WebSessionState:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise ValueError("WEB_SESSION_NOT_FOUND") from exc


def _stable_token(payload: dict[str, object]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "resume:" + hashlib.sha256(body).hexdigest()


def _origin_for_url(url: str) -> str:
    _validated_match(url, _URL_RE, "url")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("url must be an https URL.")
    origin = f"https://{parsed.hostname}"
    if parsed.port is not None:
        origin = f"{origin}:{parsed.port}"
    return origin


def _coerce_status(value: object) -> WebSessionStatus:
    if isinstance(value, WebSessionStatus):
        return value
    try:
        return WebSessionStatus(value)
    except ValueError as exc:
        raise ValueError("status is not supported.") from exc


def _validated_match(value: object, pattern: re.Pattern[str], field_name: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{field_name} is malformed.")
    return value


def _validated_positive_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer.")
    return value


def _validated_reason_code(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"^[A-Z0-9_]{3,80}$", value) is None:
        raise ValueError("reason_code is malformed.")
    return value
