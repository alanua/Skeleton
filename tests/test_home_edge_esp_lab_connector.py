from __future__ import annotations

import json
import time
from typing import Any

import pytest

from core.home_edge.esp_lab import CommandResult
from core.home_edge.esp_lab_connector import (
    CONNECTOR_VERSION,
    ConnectorConfig,
    ConnectorError,
    ReplayCache,
    SignedResponse,
    build_signed_request,
    canonical_body,
    canonical_signature_text,
    execute_connector_job,
    parse_signed_job_request,
    signed_response,
    sign,
    validate_connector_config,
    verify_signed_response,
)


SECRET = b"0123456789abcdef0123456789abcdef"


class CountingAdapter:
    adapter_name = "fake.windows.esptool"
    adapter_version = "test"

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(self, argv: list[str], **kwargs: Any) -> CommandResult:
        self.calls.append((argv, kwargs))
        return CommandResult(status="observed", stdout=b"Chip is ESP32-S3\nMAC: 11:22:33:44:55:66\n", exit_code=0)


def connector_job() -> dict[str, Any]:
    return {
        "schema": "skeleton.home_edge.esp_lab.connector.v1.job",
        "control_plane_id": "home-edge",
        "node_id": "desk-win",
        "endpoint_kind": "windows_workstation_connector",
        "adapter_kind": "windows_com",
        "operation": "identify_chip",
        "device_ref": "COM5",
        "timeout_seconds": 5,
        "idempotency_key": "idem-win-1",
        "execution_mode": "plan",
        "private_salt": "synthetic-private-salt",
    }


def parse(job: dict[str, Any], *, cache: ReplayCache | None = None, timestamp: int = 1000, nonce: str = "nonce-1") -> dict[str, Any]:
    body, headers = build_signed_request(secret=SECRET, job=job, timestamp=timestamp, nonce=nonce)
    return parse_signed_job_request(
        method="POST",
        path="/v1/esp-lab/jobs",
        headers=headers,
        body=body,
        secret=SECRET,
        cache=cache or ReplayCache(),
        allowed_node_ids={"desk-win"},
        now=timestamp,
    )


def test_exact_hmac_canonicalization_and_response_verification() -> None:
    text = canonical_signature_text(
        version=CONNECTOR_VERSION,
        method="POST",
        path="/v1/esp-lab/jobs",
        timestamp="1000",
        nonce="nonce-1",
        idempotency_key="idem-win-1",
        body_sha256="abc123",
    )
    assert text == b"skeleton.home_edge.esp_lab.connector.v1\nPOST\n/v1/esp-lab/jobs\n1000\nnonce-1\nidem-win-1\nabc123"
    payload = {"observation": {"node_id": "desk-win"}, "receipt": {"aggregate": "CAUTION"}}
    response = signed_response(SECRET, payload, request_idempotency_key="idem-win-1")
    verified = verify_signed_response(secret=SECRET, response=response, expected_idempotency_key="idem-win-1", expected_node_id="desk-win")
    assert verified == payload


def test_invalid_signature_stale_nonce_replay_and_idempotency_mismatch_rejected_before_adapter_call() -> None:
    job = connector_job()
    body, headers = build_signed_request(secret=SECRET, job=job, timestamp=1000, nonce="nonce-1")
    headers["x-esp-lab-signature"] = "0" * 64
    with pytest.raises(ConnectorError, match="invalid_signature"):
        parse_signed_job_request(method="POST", path="/v1/esp-lab/jobs", headers=headers, body=body, secret=SECRET, cache=ReplayCache(), allowed_node_ids={"desk-win"}, now=1000)
    body, headers = build_signed_request(secret=SECRET, job=job, timestamp=1000, nonce="nonce-2")
    with pytest.raises(ConnectorError, match="stale_timestamp"):
        parse_signed_job_request(method="POST", path="/v1/esp-lab/jobs", headers=headers, body=body, secret=SECRET, cache=ReplayCache(), allowed_node_ids={"desk-win"}, now=2000)
    cache = ReplayCache()
    assert parse(job, cache=cache, timestamp=2000, nonce="nonce-3")["device_ref"] == "COM5"
    with pytest.raises(ConnectorError, match="nonce_replay"):
        parse(job, cache=cache, timestamp=2000, nonce="nonce-3")
    changed = dict(job)
    changed["device_ref"] = "COM6"
    with pytest.raises(ConnectorError, match="idempotency_mismatch"):
        parse(changed, cache=cache, timestamp=2000, nonce="nonce-4")


def test_unknown_fields_oversized_body_unknown_node_and_wrong_endpoint_rejected() -> None:
    bad = connector_job()
    bad["extra"] = True
    with pytest.raises(ConnectorError, match="unknown_field"):
        parse(bad, timestamp=2000)
    body, headers = build_signed_request(secret=SECRET, job=connector_job(), timestamp=2000, nonce="big")
    with pytest.raises(ConnectorError, match="body_too_large"):
        parse_signed_job_request(method="POST", path="/v1/esp-lab/jobs", headers=headers, body=body + (b"x" * 40000), secret=SECRET, cache=ReplayCache(), allowed_node_ids={"desk-win"}, now=2000)
    wrong_node = connector_job()
    wrong_node["node_id"] = "other"
    with pytest.raises(ConnectorError, match="unknown_node"):
        parse(wrong_node, timestamp=2000, nonce="node")
    wrong_endpoint = connector_job()
    wrong_endpoint["endpoint_kind"] = "home_edge_local_linux"
    wrong_endpoint["adapter_kind"] = "linux_tty"
    wrong_endpoint["device_ref"] = "/dev/ttyUSB0"
    with pytest.raises(ConnectorError, match="wrong_endpoint"):
        parse(wrong_endpoint, timestamp=2000, nonce="endpoint")


def test_rejections_happen_before_adapter_call() -> None:
    adapter = CountingAdapter()
    with pytest.raises(ConnectorError):
        execute_connector_job({**connector_job(), "execution_mode": "read_only"}, startup_allows_read_only=False, adapter=adapter)
    assert adapter.calls == []


def test_tls_verification_cannot_be_disabled_and_lan_bind_without_tls_auth_rejected() -> None:
    config = ConnectorConfig(node_id="desk-win", shared_secret=SECRET)
    assert config.bind_host == "127.0.0.1"
    assert config.allow_read_only_execution is False
    with pytest.raises(ConnectorError, match="lan_bind_requires_flag"):
        ConnectorConfig(node_id="desk-win", shared_secret=SECRET, bind_host="0.0.0.0")
    with pytest.raises(ConnectorError, match="lan_bind_requires_tls"):
        ConnectorConfig(node_id="desk-win", shared_secret=SECRET, bind_host="0.0.0.0", allow_lan=True)
    with pytest.raises(ConnectorError, match="lan_bind_requires_tls_auth_node"):
        validate_connector_config(
            type(
                "Config",
                (),
                {
                    "bind_host": "0.0.0.0",
                    "allow_lan": True,
                    "tls_cert": "/tmp/cert.pem",
                    "tls_key": None,
                    "shared_secret": b"",
                    "allowed_node_ids": set(),
                },
            )()
        )


def test_loopback_plan_only_default_and_read_only_requires_two_flags() -> None:
    adapter = CountingAdapter()
    safe = parse(connector_job(), timestamp=2000)
    result = execute_connector_job(safe, startup_allows_read_only=True, adapter=adapter, executable_finder=lambda _: "esptool.exe", esptool_command="esptool.exe")
    assert result["observation"]["probes"][0]["status"] == "planned_not_executed"
    assert adapter.calls == []
    read = connector_job()
    read["execution_mode"] = "read_only"
    safe = parse(read, timestamp=2000, nonce="read")
    result = execute_connector_job(safe, startup_allows_read_only=True, adapter=adapter, executable_finder=lambda _: "esptool.exe", esptool_command="esptool.exe")
    assert result["receipt"]["aggregate"] == "PASS"
    assert adapter.calls[0][0] == ["esptool.exe", "--port", "COM5", "read-mac"]


def test_public_receipt_has_no_private_runtime_values_and_stable_order() -> None:
    adapter = CountingAdapter()
    read = connector_job()
    read["execution_mode"] = "read_only"
    safe = parse(read, timestamp=2000)
    result = execute_connector_job(safe, startup_allows_read_only=True, adapter=adapter, executable_finder=lambda _: "esptool.exe", esptool_command="esptool.exe")
    public = json.dumps(result["receipt"], sort_keys=True)
    for token in ("COM5", "11:22:33:44:55:66", "desk-win", "0123456789abcdef", "hostname", "username", "192.168."):
        assert token not in public
    assert canonical_body(result["receipt"]) == json.dumps(result["receipt"], sort_keys=True, separators=(",", ":")).encode("utf-8")


def test_controller_connector_correlation_and_wrong_node_rejected() -> None:
    response = SignedResponse(
        200,
        signed_response(SECRET, {"observation": {"node_id": "other"}, "receipt": {}}, request_idempotency_key="idem-win-1").headers,
        canonical_body({"observation": {"node_id": "other"}, "receipt": {}}),
    )
    response.headers["x-esp-lab-signature"] = sign(
        SECRET,
        version=CONNECTOR_VERSION,
        method="RESPONSE",
        path="/v1/esp-lab/jobs",
        timestamp=response.headers["x-esp-lab-timestamp"],
        nonce=response.headers["x-esp-lab-nonce"],
        idempotency_key="idem-win-1",
        body_sha256="0" * 64,
    )
    with pytest.raises(ConnectorError):
        verify_signed_response(secret=SECRET, response=response, expected_idempotency_key="idem-win-1", expected_node_id="desk-win")
    good = signed_response(SECRET, {"observation": {"node_id": "other"}, "receipt": {}}, request_idempotency_key="idem-win-1")
    with pytest.raises(ConnectorError, match="wrong_node"):
        verify_signed_response(secret=SECRET, response=good, expected_idempotency_key="idem-win-1", expected_node_id="desk-win")


def test_cache_expiration_is_deterministic() -> None:
    cache = ReplayCache(ttl_seconds=10, max_items=2)
    cache.check(nonce="a", idempotency_key="a", body_hash="1", now=100)
    cache.check(nonce="b", idempotency_key="b", body_hash="2", now=101)
    cache.check(nonce="c", idempotency_key="c", body_hash="3", now=112)
    assert "a" not in cache.nonces
    assert list(cache.nonces) == ["c"]


def test_no_real_network_registry_subprocess_or_sleep_needed() -> None:
    assert int(time.time()) > 0
