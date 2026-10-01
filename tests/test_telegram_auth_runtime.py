from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.telegram_auth_runtime import (
    TelegramAuthRuntimeError,
    TelegramAuthStateStore,
    assert_no_secret_material,
    normalize_sent_code_delivery,
    resend_auth_code,
    safe_auth_response_fields,
    start_auth,
)


class SentCodeTypeApp:
    def __init__(self, length: int = 5) -> None:
        self.length = length


class SentCodeTypeSms:
    def __init__(self, length: int = 6) -> None:
        self.length = length


class SentCodeTypeEmailCode:
    def __init__(self, length: int = 5) -> None:
        self.length = length


class SentCodeTypeMissedCall:
    def __init__(self, length: int = 4) -> None:
        self.length = length


class FloodWaitError(Exception):
    def __init__(self, seconds: int) -> None:
        self.seconds = seconds


class PhoneCodeExpiredError(Exception):
    pass


class SendCodeUnavailableError(Exception):
    pass


class FakeSession:
    def __init__(self, value: str) -> None:
        self.value = value

    def save(self) -> str:
        return self.value


class FakeClient:
    def __init__(self, first: object, resent: object | Exception | None = None) -> None:
        self.first = first
        self.resent = resent
        self.session = FakeSession("secret-session-v1")
        self.send_code_calls = 0
        self.raw_requests: list[object] = []

    def send_code_request(self, phone: str) -> object:
        assert phone == "+15551230000"
        self.send_code_calls += 1
        return self.first

    def __call__(self, request: object) -> object:
        self.raw_requests.append(request)
        if isinstance(self.resent, Exception):
            raise self.resent
        self.session.value = "secret-session-v2"
        return self.resent


def sent_code(
    *,
    type_: object,
    phone_code_hash: str = "secret-hash-v1",
    next_type: object | None = None,
    timeout: int | None = None,
) -> object:
    return SimpleNamespace(type=type_, phone_code_hash=phone_code_hash, next_type=next_type, timeout=timeout)


def test_sent_code_type_app_is_safe_and_public() -> None:
    delivery = normalize_sent_code_delivery(sent_code(type_=SentCodeTypeApp(length=5)))

    assert delivery.to_response() == {"delivery_type": "APP", "code_length": 5}


def test_sent_code_type_sms_is_safe_and_public() -> None:
    delivery = normalize_sent_code_delivery(sent_code(type_=SentCodeTypeSms(length=6)))

    assert delivery.to_response() == {"delivery_type": "SMS", "code_length": 6}


def test_next_type_and_timeout_are_normalized() -> None:
    delivery = normalize_sent_code_delivery(
        sent_code(type_=SentCodeTypeApp(), next_type=SentCodeTypeEmailCode(), timeout=30)
    )

    assert delivery.to_response() == {
        "delivery_type": "APP",
        "next_delivery_type": "EMAIL",
        "timeout_seconds": 30,
        "code_length": 5,
    }


def test_resend_before_timeout_is_rejected_locally_without_telegram_call() -> None:
    store = TelegramAuthStateStore()
    client = FakeClient(sent_code(type_=SentCodeTypeApp(), next_type=SentCodeTypeSms(), timeout=60))
    start_auth(client, store, phone="+15551230000", now=1000)

    with pytest.raises(TelegramAuthRuntimeError) as exc:
        resend_auth_code(client, store, now=1020)

    assert exc.value.reason_code == "RESEND_NOT_READY"
    assert exc.value.retry_after_seconds == 40
    assert client.raw_requests == []
    assert client.send_code_calls == 1


def test_successful_resend_uses_raw_request_and_updates_hash_session_and_safe_delivery() -> None:
    store = TelegramAuthStateStore()
    client = FakeClient(
        sent_code(type_=SentCodeTypeApp(), next_type=SentCodeTypeSms(), timeout=10),
        resent=sent_code(type_=SentCodeTypeSms(length=6), phone_code_hash="secret-hash-v2"),
    )
    start_auth(client, store, phone="+15551230000", now=1000)

    response = resend_auth_code(client, store, now=1011)
    state = store.get(now=1011)

    assert response["status"] == "CODE_SENT"
    assert response["delivery_type"] == "SMS"
    assert state.phone_code_hash == "secret-hash-v2"
    assert state.session == "secret-session-v2"
    assert len(client.raw_requests) == 1
    assert type(client.raw_requests[0]).__name__.endswith("ResendCodeRequest")
    assert client.send_code_calls == 1


def test_flood_wait_preserves_exact_retry_after_seconds() -> None:
    store = TelegramAuthStateStore()
    client = FakeClient(
        sent_code(type_=SentCodeTypeApp(), next_type=SentCodeTypeSms(), timeout=0),
        resent=FloodWaitError(313),
    )
    start_auth(client, store, phone="+15551230000", now=1000)

    with pytest.raises(TelegramAuthRuntimeError) as exc:
        resend_auth_code(client, store, now=1000)

    assert exc.value.reason_code == "FLOOD_WAIT"
    assert exc.value.retry_after_seconds == 313


def test_phone_code_expired_maps_to_auth_expired_reset_path() -> None:
    store = TelegramAuthStateStore()
    client = FakeClient(
        sent_code(type_=SentCodeTypeApp(), next_type=SentCodeTypeSms(), timeout=0),
        resent=PhoneCodeExpiredError(),
    )
    start_auth(client, store, phone="+15551230000", now=1000)

    with pytest.raises(TelegramAuthRuntimeError) as exc:
        resend_auth_code(client, store, now=1000)

    assert exc.value.reason_code == "AUTH_EXPIRED"
    assert exc.value.to_response()["status"] == "AUTH_EXPIRED"


def test_expired_local_auth_state_returns_stable_auth_expired() -> None:
    store = TelegramAuthStateStore()
    client = FakeClient(sent_code(type_=SentCodeTypeApp(), next_type=SentCodeTypeSms(), timeout=0))
    start_auth(client, store, phone="+15551230000", now=1000, ttl_seconds=5)

    with pytest.raises(TelegramAuthRuntimeError) as exc:
        resend_auth_code(client, store, now=1006)

    assert exc.value.reason_code == "AUTH_EXPIRED"


def test_send_code_unavailable_has_stable_reason_code() -> None:
    store = TelegramAuthStateStore()
    client = FakeClient(
        sent_code(type_=SentCodeTypeApp(), next_type=SentCodeTypeSms(), timeout=0),
        resent=SendCodeUnavailableError(),
    )
    start_auth(client, store, phone="+15551230000", now=1000)

    with pytest.raises(TelegramAuthRuntimeError) as exc:
        resend_auth_code(client, store, now=1000)

    assert exc.value.reason_code == "SEND_CODE_UNAVAILABLE"


def test_response_and_fixture_text_do_not_contain_secret_material() -> None:
    store = TelegramAuthStateStore()
    client = FakeClient(
        sent_code(type_=SentCodeTypeApp(), phone_code_hash="secret-hash-v1", next_type=SentCodeTypeSms(), timeout=0),
        resent=sent_code(type_=SentCodeTypeSms(), phone_code_hash="secret-hash-v2"),
    )
    start_response = start_auth(client, store, phone="+15551230000", now=1000)
    resend_response = resend_auth_code(client, store, now=1000)
    encoded = json.dumps({"start": start_response, "resend": resend_response}, sort_keys=True)

    assert safe_auth_response_fields(start_response) == {
        "code_length": 5,
        "delivery_type": "APP",
        "next_delivery_type": "SMS",
        "timeout_seconds": 0,
    }
    assert_no_secret_material(
        encoded,
        {
            "phone": "+15551230000",
            "phone_code_hash": "secret-hash-v",
            "session": "secret-session",
            "code": "12345",
            "password": "correct-horse",
        },
    )
