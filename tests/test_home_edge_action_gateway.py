from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from core.home_edge.action_gateway import (
    HomeEdgeActionGateway,
    HomeEdgeGatewayError,
    sign_body,
)
from core.home_edge.diagnostics import ProbeResult


NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
SECRET = b"test-secret"


class CountingTransport:
    def __init__(self) -> None:
        self.calls = 0

    def run_action(self, payload: str, *, timeout_seconds: int) -> ProbeResult:
        self.calls += 1
        return ProbeResult(
            state="observed",
            adapter="fake",
            stdout=json.dumps({"status": "observed", "value": {"level": 50}}),
            exit_code=0,
        )


def request(**overrides: object) -> dict:
    body = {
        "node_id": "home-edge-01",
        "action_id": "media.set_volume",
        "request_id": "req-00000001",
        "timestamp": NOW.isoformat(),
        "nonce": "nonce000000000001",
        "idempotency_key": "idem-00000001",
        "parameters": {"level": 50, "target": "host"},
    }
    body.update(overrides)
    return body


def encode(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def call(gateway: HomeEdgeActionGateway, body: dict) -> dict:
    payload = encode(body)
    return gateway.handle_json(
        payload,
        key_id="test-key",
        signature=sign_body(payload, SECRET),
        now=NOW,
    )


def test_same_idempotency_key_and_body_executes_once() -> None:
    transport = CountingTransport()
    gateway = HomeEdgeActionGateway(SECRET, "test-key", transport=transport)
    body = request()

    first = call(gateway, body)
    second = call(gateway, body)

    assert transport.calls == 1
    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    assert second["result"] == first["result"]


def test_same_idempotency_key_different_body_rejected() -> None:
    gateway = HomeEdgeActionGateway(SECRET, "test-key", transport=CountingTransport())
    call(gateway, request())

    with pytest.raises(HomeEdgeGatewayError, match="different body"):
        call(gateway, request(parameters={"level": 51, "target": "host"}))


def test_stale_timestamp_and_repeated_nonce_rejected() -> None:
    gateway = HomeEdgeActionGateway(SECRET, "test-key", transport=CountingTransport())
    stale = request(
        timestamp=(NOW - timedelta(minutes=10)).isoformat(),
        idempotency_key="idem-00000002",
        nonce="nonce000000000002",
    )
    with pytest.raises(HomeEdgeGatewayError, match="stale timestamp"):
        call(gateway, stale)

    call(gateway, request(idempotency_key="idem-00000003", nonce="nonce000000000003"))
    with pytest.raises(HomeEdgeGatewayError, match="nonce replay"):
        call(gateway, request(idempotency_key="idem-00000004", nonce="nonce000000000003"))


def test_wrong_auth_rejected_before_transport() -> None:
    transport = CountingTransport()
    gateway = HomeEdgeActionGateway(SECRET, "test-key", transport=transport)
    payload = encode(request())

    with pytest.raises(HomeEdgeGatewayError, match="authentication failed"):
        gateway.handle_json(
            payload,
            key_id="wrong",
            signature=sign_body(payload, SECRET),
            now=NOW,
        )

    assert transport.calls == 0


def test_wrong_node_and_action_rejected_before_transport() -> None:
    transport = CountingTransport()
    gateway = HomeEdgeActionGateway(SECRET, "test-key", transport=transport)

    with pytest.raises(Exception, match="wrong node"):
        call(gateway, request(node_id="other"))
    with pytest.raises(Exception, match="not allowlisted"):
        call(gateway, request(action_id="media.shell"))

    assert transport.calls == 0


def test_unauthenticated_status_hides_capabilities() -> None:
    gateway = HomeEdgeActionGateway(SECRET, "test-key", transport=CountingTransport())

    status = gateway.public_unauthenticated_status()

    assert status == {
        "schema": "skeleton.home_edge.action_gateway.status.v1",
        "status": "authentication_required",
    }
    assert "home-edge-01" not in json.dumps(status)
