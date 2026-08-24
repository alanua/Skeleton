from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Mapping

import pytest

from core.home_edge.esp_lab_activation import (
    EXECUTION_LANE,
    IDEMPOTENCY_KEY_PREFIX,
    OPERATOR_APPROVAL_REF,
    RUN_AS,
    TARGET_NODE,
    EspLabActivationError,
    HmacEspLabRequestSigner,
    build_esp_lab_controller_request,
    dispatch_esp_lab_controller_request,
    sign_esp_lab_controller_request,
)
from core.home_edge.executor import HomeEdgeExecReceipt, HomeEdgeExecRequest, sign_request


SECRET = "synthetic-esp-lab-controller-secret"


def read_only_job(**updates: Any) -> dict[str, Any]:
    job: dict[str, Any] = {
        "schema": "skeleton.home_edge.esp_lab.v1.job",
        "control_plane_id": "home-edge",
        "node_id": "esp-bench",
        "endpoint_kind": "windows_workstation_connector",
        "adapter_kind": "windows_com",
        "operation": "identify_chip",
        "device_ref": r"\\.\COM42",
        "timeout_seconds": 5,
        "idempotency_key": "esp-read-only-1",
        "execution_mode": "read_only",
        "private_salt": "synthetic-private-salt",
    }
    job.update(updates)
    return job


def receipt_for(request: HomeEdgeExecRequest) -> HomeEdgeExecReceipt:
    now = datetime.now(UTC).isoformat()
    return HomeEdgeExecReceipt(
        status="ok",
        request_id=request.request_id,
        node_id=request.node_id,
        execution_lane=request.execution_lane.value,
        exit_code=0,
        stdout=json.dumps({"receipt": {"aggregate": "PASS"}}),
        stderr="",
        started_at=now,
        finished_at=now,
        duration_seconds=0.01,
        idempotency="executed",
        receipt_hash="a" * 64,
    )


def test_builds_exact_universal_home_edge_executor_request_for_read_only_esp_lab_job() -> None:
    request = build_esp_lab_controller_request(
        read_only_job(),
        request_id="home_edge_esp_lab_stage1a_controller_v1-fixed",
        idempotency_key=f"{IDEMPOTENCY_KEY_PREFIX}-fixed",
        nonce="home_edge_esp_lab_stage1a_controller_v1-fixed-nonce",
        timestamp="2026-08-24T00:00:00+00:00",
    )

    payload = request.to_mapping(include_signature=False)
    assert payload["schema"] == "skeleton.home_edge.exec_request.v1"
    assert payload["node_id"] == TARGET_NODE
    assert payload["execution_lane"] == EXECUTION_LANE
    assert payload["run_as"] == RUN_AS
    assert payload["operator_approval_ref"] == OPERATOR_APPROVAL_REF
    assert payload["mode"] == "script"
    assert payload["script_interpreter"] == "python3"
    assert payload["argv"] == []
    assert payload["environment"] == {}
    assert payload["public"] is False
    assert json.loads(payload["stdin_text"])["device_ref"] == "COM42"


def test_signer_applies_hmac_signature_without_changing_authority() -> None:
    unsigned = build_esp_lab_controller_request(read_only_job())
    signed = sign_esp_lab_controller_request(unsigned, signer=HmacEspLabRequestSigner(SECRET))

    assert signed.to_mapping(include_signature=False) == unsigned.to_mapping(include_signature=False)
    assert signed.signature == sign_request(signed, SECRET)


def test_missing_or_wrong_approval_blocks_before_signer() -> None:
    unsigned = build_esp_lab_controller_request(read_only_job()).to_mapping(include_signature=False)
    unsigned["operator_approval_ref"] = "wrong"
    request = HomeEdgeExecRequest.from_mapping(unsigned)

    def fail_signer(_request: Mapping[str, Any]) -> HomeEdgeExecRequest:
        pytest.fail("signer must not run")

    with pytest.raises(EspLabActivationError, match="operator_approval_mismatch"):
        sign_esp_lab_controller_request(request, signer=fail_signer)


def test_altered_signed_request_blocks_before_dispatch() -> None:
    def altered_signer(unsigned: Mapping[str, Any]) -> HomeEdgeExecRequest:
        signed = HmacEspLabRequestSigner(SECRET)(unsigned)
        return HomeEdgeExecRequest.from_mapping(
            {
                **signed.to_mapping(include_signature=True),
                "idempotency_key": f"{IDEMPOTENCY_KEY_PREFIX}-changed",
            }
        )

    with pytest.raises(EspLabActivationError, match="signed_authority_mismatch"):
        dispatch_esp_lab_controller_request(
            read_only_job(),
            signer=altered_signer,
            dispatcher=lambda _request: pytest.fail("dispatcher must not run"),
        )


def test_dispatcher_receives_only_signed_executor_request() -> None:
    calls: list[Mapping[str, Any]] = []

    def dispatcher(request: Mapping[str, Any]) -> HomeEdgeExecReceipt:
        calls.append(request)
        return receipt_for(HomeEdgeExecRequest.from_mapping(request))

    receipt = dispatch_esp_lab_controller_request(
        read_only_job(),
        signer=HmacEspLabRequestSigner(SECRET),
        dispatcher=dispatcher,
    )

    assert receipt.status == "ok"
    assert len(calls) == 1
    request = HomeEdgeExecRequest.from_mapping(calls[0])
    assert request.signature == sign_request(request, SECRET)
    assert request.node_id == TARGET_NODE
    assert request.execution_lane.value == EXECUTION_LANE
    assert request.run_as.value == RUN_AS


def test_plan_jobs_and_unknown_job_fields_are_not_activation_authority() -> None:
    with pytest.raises(EspLabActivationError, match="requires_read_only"):
        build_esp_lab_controller_request(read_only_job(execution_mode="plan"))
    with pytest.raises(EspLabActivationError, match="job_invalid"):
        build_esp_lab_controller_request(read_only_job(extra=True))
