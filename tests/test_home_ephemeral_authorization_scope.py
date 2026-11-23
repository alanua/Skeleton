from __future__ import annotations

from dataclasses import replace
import pytest

from core.home_ephemeral_authorization_scope import (
    AuthorizationView, RequestScope, scoped_ephemeral_views,
)


NOW = 1000
URL = "https://real-debrid.com/device"
URLS = {"real-debrid": (URL,)}
NONCE = "safe_flow_nonce_1234567890"


def ctx(**changes):
    base = RequestScope(
        owner_id="operator-A", device_id="verified-hp", session_id="session-A",
        flow_nonce=NONCE, role="full", authenticated=True,
        encrypted_transport=True, lost_device=False,
    )
    return replace(base, **changes)


def row(**changes):
    base = {
        "service": "real-debrid",
        "label": "Real-Debrid",
        "code": "USER-ABC123",
        "verification_url": URL,
        "expires_at": NOW + 300,
        "status": "active",
        "owner_id": "operator-A",
        "device_id": "verified-hp",
        "session_id": "session-A",
        "flow_nonce": NONCE,
        "device_code": "BACKEND_ONLY_DEVICE_CODE",
        "refresh_token": "SHOULD_NOT_APPEAR",
    }
    base.update(changes)
    return base


def visible(rows, scope=None, **kwargs):
    return scoped_ephemeral_views(
        rows, scope if scope is not None else ctx(), now=NOW,
        verification_urls=URLS, **kwargs,
    )


def test_matching_operator_sees_only_safe_display_fields():
    result = visible([row()])
    assert len(result) == 1
    assert isinstance(result[0], AuthorizationView)
    payload = result[0].to_operator_payload()
    assert payload == {
        "service": "real-debrid", "label": "Real-Debrid",
        "code": "USER-ABC123", "verification_url": URL,
        "expires_at": NOW + 300, "status": "active",
    }
    for forbidden in ("device_code", "refresh_token", "client_secret", "access_token"):
        assert forbidden not in payload
    assert "USER-ABC123" not in repr(result)
    assert "operator-A" not in repr(ctx())


def test_other_operator_cannot_read_code():
    assert visible([row()], ctx(owner_id="operator-B")) == ()


def test_same_owner_different_device_cannot_read_code():
    assert visible([row()], ctx(device_id="another-device")) == ()


def test_same_owner_device_different_session_cannot_read_code():
    assert visible([row()], ctx(session_id="session-B")) == ()


def test_wrong_flow_nonce_cannot_read_code():
    assert visible([row()], ctx(flow_nonce="other_flow_nonce_1234567890")) == ()


@pytest.mark.parametrize("scope", [
    ctx(authenticated=False),
    ctx(encrypted_transport=False),
    ctx(role="family"),
    ctx(role="unknown"),
    ctx(lost_device=True),
    ctx(owner_id=""),
    ctx(session_id=""),
    ctx(device_id=""),
    ctx(flow_nonce="short"),
])
def test_untrusted_scope_has_no_code_access(scope):
    assert visible([row()], scope) == ()


def test_unowned_legacy_broker_code_hidden():
    old = row()
    for field in ("owner_id", "device_id", "session_id", "flow_nonce"):
        old.pop(field)
    assert visible([old]) == ()


@pytest.mark.parametrize("field", ["owner_id", "device_id", "session_id", "flow_nonce"])
def test_each_missing_binding_fails_closed(field):
    old = row()
    old.pop(field)
    assert visible([old]) == ()


def test_other_full_profile_device_cannot_cross_read():
    a = ctx()
    b = ctx(owner_id="operator-B", device_id="verified-b", session_id="session-B",
            flow_nonce="nonce_for_other_B_1234567890")
    other = row(owner_id=b.owner_id, device_id=b.device_id,
                session_id=b.session_id, flow_nonce=b.flow_nonce, code="CODE_FOR_B")
    result_a = visible([row(), other], a)
    result_b = visible([row(), other], b)
    assert len(result_a) == len(result_b) == 1
    assert result_a[0].to_operator_payload()["code"] == "USER-ABC123"
    assert result_b[0].to_operator_payload()["code"] == "CODE_FOR_B"


def test_missing_registered_url_fails_closed():
    assert scoped_ephemeral_views([row()], ctx(), now=NOW, verification_urls={}) == ()


@pytest.mark.parametrize("url", [
    "http://real-debrid.com/device",
    "https://real-debrid.com.evil.invalid/device",
    "https://real-debrid.com/device?code=SECRET",
    "https://evil.invalid/other",
])
def test_unregistered_or_malicious_verification_url_hidden(url):
    assert visible([row(verification_url=url)]) == ()


@pytest.mark.parametrize("change", [
    {"status": "used"},
    {"status": "expired"},
    {"expires_at": NOW},
    {"expires_at": NOW - 1},
    {"expires_at": NOW + 90000},
    {"expires_at": "invalid"},
    {"code": ""},
    {"code": None},
    {"service": "unauthorized-service"},
])
def test_invalid_or_expired_record_is_hidden(change):
    assert visible([row(**change)]) == ()


def test_bounded_results_and_order():
    rows = [row(code=f"CODE-{i}") for i in range(12)]
    result = visible(rows, max_results=2)
    assert [x.to_operator_payload()["code"] for x in result] == ["CODE-0", "CODE-1"]


def test_invalid_clock_and_limit_fail_closed():
    assert scoped_ephemeral_views([row()], ctx(), now=-1, verification_urls=URLS) == ()
    assert visible([row()], max_results=0) == ()
    assert visible([row()], max_results=33) == ()


def test_invalid_row_shapes_are_ignored():
    assert visible([None, "bad", [], {}, row()]) == visible([row()])


def test_broker_filter_is_pure_and_does_not_mutate_live_state():
    records = [row(), row(owner_id="operator-B")]
    snapshot = [dict(r) for r in records]
    visible(records)
    assert records == snapshot


def test_unexpected_unicode_identity_fails_closed_without_crashing():
    assert visible([row(owner_id="невалідний")]) == ()


def test_url_allowlist_requires_https_and_tuple_of_strings():
    assert scoped_ephemeral_views([row()], ctx(), now=NOW, verification_urls={"real-debrid": ("http://real-debrid.com/device",)}) == ()
    assert scoped_ephemeral_views([row()], ctx(), now=NOW, verification_urls={"real-debrid": [URL]}) == ()
