from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping


AUTH_STATE_TTL_SECONDS = 600
TELEGRAM_AUTH_START_ENDPOINT = "/api/native/home-edge/telegram/auth/start"
TELEGRAM_AUTH_RESEND_ENDPOINT = "/api/native/home-edge/telegram/auth/resend"
SAFE_DELIVERY_FIELDS = frozenset({"delivery_type", "next_delivery_type", "timeout_seconds", "code_length"})
SECRET_STATE_FIELDS = frozenset({"phone", "phone_code_hash", "session", "api_hash", "code", "password"})


class TelegramAuthRuntimeError(RuntimeError):
    def __init__(self, reason_code: str, message: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.retry_after_seconds = retry_after_seconds

    def to_response(self) -> dict[str, Any]:
        response: dict[str, Any] = {"status": self.reason_code, "reason_code": self.reason_code, "error": str(self)}
        if self.retry_after_seconds is not None:
            response["retry_after_seconds"] = self.retry_after_seconds
        return response


@dataclass(frozen=True)
class TelegramAuthDelivery:
    delivery_type: str
    next_delivery_type: str | None = None
    timeout_seconds: int | None = None
    code_length: int | None = None

    def to_response(self) -> dict[str, Any]:
        response: dict[str, Any] = {"delivery_type": self.delivery_type}
        if self.next_delivery_type is not None:
            response["next_delivery_type"] = self.next_delivery_type
        if self.timeout_seconds is not None:
            response["timeout_seconds"] = self.timeout_seconds
        if self.code_length is not None:
            response["code_length"] = self.code_length
        return response


@dataclass(frozen=True)
class TelegramEphemeralAuthState:
    phone: str
    phone_code_hash: str
    session: str
    delivery: TelegramAuthDelivery
    created_at: float
    updated_at: float
    expires_at: float

    def safe_response(self) -> dict[str, Any]:
        return self.delivery.to_response()


class TelegramAuthStateStore:
    """Single-user ephemeral MTProto login state for Home Edge auth handoff."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: TelegramEphemeralAuthState | None = None

    def get(self, *, now: float | None = None) -> TelegramEphemeralAuthState:
        with self._lock:
            state = self._state
            if state is None:
                raise TelegramAuthRuntimeError("AUTH_EXPIRED", "Telegram auth state expired")
            if (now if now is not None else time.time()) >= state.expires_at:
                self._state = None
                raise TelegramAuthRuntimeError("AUTH_EXPIRED", "Telegram auth state expired")
            return state

    def set(self, state: TelegramEphemeralAuthState) -> TelegramEphemeralAuthState:
        with self._lock:
            self._state = state
            return state

    def update(self, updater: Callable[[TelegramEphemeralAuthState], TelegramEphemeralAuthState], *, now: float | None = None) -> TelegramEphemeralAuthState:
        with self._lock:
            state = self._state
            if state is None or (now if now is not None else time.time()) >= state.expires_at:
                self._state = None
                raise TelegramAuthRuntimeError("AUTH_EXPIRED", "Telegram auth state expired")
            updated = updater(state)
            self._state = updated
            return updated

    def clear(self) -> None:
        with self._lock:
            self._state = None


def auth_start_response(sent_code: Any, *, message: str = "Код Telegram надіслано безпечним способом.") -> dict[str, Any]:
    delivery = normalize_sent_code_delivery(sent_code)
    return {"status": "CODE_SENT", "message": message, **delivery.to_response()}


def start_auth(
    client: Any,
    store: TelegramAuthStateStore,
    *,
    phone: str,
    now: float | None = None,
    ttl_seconds: int = AUTH_STATE_TTL_SECONDS,
) -> dict[str, Any]:
    current = now if now is not None else time.time()
    try:
        sent_code = _awaitless_call(getattr(client, "send_code_request"), phone)
    except Exception as exc:  # noqa: BLE001 - normalized into stable auth outcomes.
        raise _stable_auth_error(exc) from exc
    phone_code_hash = _safe_required_attr(sent_code, "phone_code_hash", "PHONE_CODE_HASH_MISSING")
    delivery = normalize_sent_code_delivery(sent_code)
    store.set(
        TelegramEphemeralAuthState(
            phone=phone,
            phone_code_hash=phone_code_hash,
            session=_extract_session(client),
            delivery=delivery,
            created_at=current,
            updated_at=current,
            expires_at=current + max(1, int(ttl_seconds)),
        )
    )
    return {"status": "CODE_SENT", "message": delivery_message(delivery), **delivery.to_response()}


def resend_auth_code(
    client: Any,
    store: TelegramAuthStateStore,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    current = now if now is not None else time.time()
    state = store.get(now=current)
    retry_after = resend_retry_after_seconds(state, now=current)
    if retry_after is not None and retry_after > 0:
        raise TelegramAuthRuntimeError(
            "RESEND_NOT_READY",
            "Telegram resend is not available yet",
            retry_after_seconds=retry_after,
        )
    try:
        sent_code = _awaitless_call(_raw_resend, client, state.phone, state.phone_code_hash)
    except Exception as exc:  # noqa: BLE001 - normalized into stable auth outcomes.
        raise _stable_auth_error(exc) from exc
    phone_code_hash = _safe_required_attr(sent_code, "phone_code_hash", "PHONE_CODE_HASH_MISSING")
    delivery = normalize_sent_code_delivery(sent_code)

    def apply_success(previous: TelegramEphemeralAuthState) -> TelegramEphemeralAuthState:
        return replace(
            previous,
            phone_code_hash=phone_code_hash,
            session=_extract_session(client),
            delivery=delivery,
            updated_at=current,
        )

    store.update(apply_success, now=current)
    return {"status": "CODE_SENT", "message": delivery_message(delivery), **delivery.to_response()}


def resend_retry_after_seconds(state: TelegramEphemeralAuthState, *, now: float | None = None) -> int | None:
    timeout = state.delivery.timeout_seconds
    if timeout is None or state.delivery.next_delivery_type is None:
        return None
    ready_at = state.updated_at + max(0, timeout)
    remaining = int(round(ready_at - (now if now is not None else time.time())))
    return max(0, remaining)


def normalize_sent_code_delivery(sent_code: Any) -> TelegramAuthDelivery:
    return TelegramAuthDelivery(
        delivery_type=normalize_delivery_type(getattr(sent_code, "type", None)),
        next_delivery_type=_optional_delivery_type(getattr(sent_code, "next_type", None)),
        timeout_seconds=_optional_int(getattr(sent_code, "timeout", None), minimum=0, maximum=86_400),
        code_length=_optional_int(
            getattr(getattr(sent_code, "type", None), "length", None)
            or getattr(sent_code, "length", None),
            minimum=1,
            maximum=16,
        ),
    )


def normalize_delivery_type(value: Any) -> str:
    name = type(value).__name__ if value is not None else ""
    normalized = "".join(ch for ch in name.lower() if ch.isalnum())
    if normalized.endswith("type"):
        normalized = normalized[:-4]
    if normalized.startswith("sentcodetype"):
        normalized = normalized.removeprefix("sentcodetype")
    if normalized in {"app"}:
        return "APP"
    if normalized in {"sms"}:
        return "SMS"
    if normalized in {"call"}:
        return "CALL"
    if normalized in {"missedcall", "flashcall"}:
        return "MISSED_CALL"
    if "email" in normalized:
        return "EMAIL"
    if "fragment" in normalized:
        return "FRAGMENT"
    if "firebase" in normalized:
        return "FIREBASE"
    return "UNSUPPORTED_THIRD_PARTY"


def delivery_message(delivery: TelegramAuthDelivery) -> str:
    label = {
        "APP": "у Telegram на вже авторизованому пристрої",
        "SMS": "через SMS",
        "CALL": "через дзвінок",
        "MISSED_CALL": "через пропущений дзвінок",
        "EMAIL": "на email",
        "FRAGMENT": "через Fragment",
        "FIREBASE": "через сторонній канал Telegram",
        "UNSUPPORTED_THIRD_PARTY": "через канал Telegram, який Home не показує як секрет",
    }.get(delivery.delivery_type, "безпечним способом")
    if delivery.next_delivery_type and delivery.timeout_seconds is not None:
        return f"Код Telegram надіслано {label}. Інший спосіб буде доступний за {delivery.timeout_seconds} с."
    return f"Код Telegram надіслано {label}."


def safe_auth_response_fields(response: Mapping[str, Any]) -> dict[str, Any]:
    return {key: response[key] for key in sorted(SAFE_DELIVERY_FIELDS & set(response))}


def assert_no_secret_material(value: Any, secret_material: Mapping[str, str]) -> None:
    encoded = str(value)
    for label, secret in secret_material.items():
        if secret and secret in encoded:
            raise AssertionError(f"secret material leaked: {label}")


def _optional_delivery_type(value: Any) -> str | None:
    if value is None:
        return None
    return normalize_delivery_type(value)


def _optional_int(value: Any, *, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if minimum <= number <= maximum else None


def _raw_resend(client: Any, phone: str, phone_code_hash: str) -> Any:
    request = _resend_code_request(phone, phone_code_hash)
    return client(request)


def _resend_code_request(phone: str, phone_code_hash: str) -> Any:
    try:
        from telethon.tl.functions import auth  # type: ignore[import-not-found]

        return auth.ResendCodeRequest(phone, phone_code_hash)
    except Exception:
        return _FallbackResendCodeRequest(phone, phone_code_hash)


@dataclass(frozen=True)
class _FallbackResendCodeRequest:
    phone_number: str
    phone_code_hash: str


def _stable_auth_error(exc: Exception) -> TelegramAuthRuntimeError:
    name = type(exc).__name__
    seconds = getattr(exc, "seconds", None) or getattr(exc, "value", None)
    if seconds is not None or "FloodWait" in name:
        return TelegramAuthRuntimeError("FLOOD_WAIT", "Telegram flood wait", retry_after_seconds=max(0, int(seconds or 0)))
    if "PhoneCodeExpired" in name:
        return TelegramAuthRuntimeError("AUTH_EXPIRED", "Telegram auth state expired")
    if "SendCodeUnavailable" in name:
        return TelegramAuthRuntimeError("SEND_CODE_UNAVAILABLE", "Telegram cannot resend this code")
    return TelegramAuthRuntimeError("TELEGRAM_AUTH_FAILED", "Telegram auth request failed")


def _safe_required_attr(value: Any, attr: str, reason_code: str) -> str:
    material = getattr(value, attr, None)
    if not isinstance(material, str) or not material:
        raise TelegramAuthRuntimeError(reason_code, "Telegram auth response was incomplete")
    return material


def _extract_session(client: Any) -> str:
    session = getattr(client, "session", None)
    save = getattr(session, "save", None)
    if callable(save):
        value = save()
        return str(value or "")
    return str(session or "")


def _awaitless_call(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    value = func(*args, **kwargs)
    if hasattr(value, "__await__"):
        raise TelegramAuthRuntimeError("ASYNC_CLIENT_UNSUPPORTED", "Telegram auth runtime expected a synchronous Telethon client")
    return value
