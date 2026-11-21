import pytest

from core.device_capability_contract import DeviceActionPlan, DeviceRisk
from core.device_capability_dispatch import (
    DeviceCapabilityError, execute_device_capability, prepare_home_edge_request,
)


def plan(**overrides):
    data = dict(
        device_id="brother_printer", capability="print_head_clean_black_normal",
        node_id="home-edge-01", execution_lane=DeviceRisk.ROUTINE_MUTATION,
        run_as="desktop-user", timeout_seconds=90,
        request_id="req-1", idempotency_key="once-1",
        argv=("/usr/local/bin/reviewed-brother-adapter", "clean-black-normal"),
        operator_approval_ref="signed-receipt", requires_secret=True,
        secret_ref="bitwarden-sm:opaque-ref", verification="physical output",
    )
    data.update(overrides)
    return DeviceActionPlan(**data)


def valid_receipt(**overrides):
    data = {
        "schema": "skeleton.home_edge.exec_receipt.v1",
        "status": "ok",
        "request_id": "req-1",
        "node_id": "home-edge-01",
        "execution_lane": "routine_mutation",
        "exit_code": 0,
        "idempotency": "executed",
        "receipt_hash": "test-hash",
    }
    data.update(overrides)
    return data


def test_dispatch_uses_only_canonical_gateway():
    called = []
    def transport(request):
        called.append(request)
        return valid_receipt()
    result = execute_device_capability(
        "brother_printer", "print_head_clean_black_normal",
        resolve=lambda *_: plan(),
        verify_approval=lambda *args: True,
        execute_home_edge=transport,
        verify_postcondition=lambda *_: True,
    )
    assert result.status == "physically_verified"
    assert called[0]["node_id"] == "home-edge-01"
    assert called[0]["mode"] == "argv"
    assert "bitwarden-sm" not in str(called[0])


def test_rejects_unverified_operator_approval():
    with pytest.raises(DeviceCapabilityError, match="invalid"):
        prepare_home_edge_request(plan(), approval_verifier=lambda *args: False)


def test_rejects_incorrect_resolved_device_identity():
    with pytest.raises(DeviceCapabilityError, match="identity mismatch"):
        execute_device_capability(
            "other_device", "print_head_clean_black_normal",
            resolve=lambda *_: plan(),
            verify_approval=lambda *args: True,
            execute_home_edge=lambda req: valid_receipt(),
            verify_postcondition=lambda *_: True,
        )


def test_execution_success_without_physical_evidence_remains_unverified():
    result = execute_device_capability(
        "brother_printer", "print_head_clean_black_normal",
        resolve=lambda *_: plan(),
        verify_approval=lambda *args: True,
        execute_home_edge=lambda req: valid_receipt(),
        verify_postcondition=lambda *_: False,
    )
    assert result.status == "accepted_unverified"
    assert not result.verified


def test_authentication_backend_failure_is_fail_closed():
    def fail(*args):
        raise ConnectionError("down")
    with pytest.raises(DeviceCapabilityError, match="unavailable"):
        prepare_home_edge_request(plan(), approval_verifier=fail)


def test_rejects_mismatched_receipt_request_id():
    with pytest.raises(DeviceCapabilityError, match="receipt mismatch"):
        execute_device_capability(
            "brother_printer", "print_head_clean_black_normal",
            resolve=lambda *_: plan(), verify_approval=lambda *_: True,
            execute_home_edge=lambda req: valid_receipt(request_id="another-request"),
            verify_postcondition=lambda *_: True,
        )


def test_nonzero_exit_cannot_claim_physical_verification():
    result = execute_device_capability(
        "brother_printer", "print_head_clean_black_normal",
        resolve=lambda *_: plan(), verify_approval=lambda *_: True,
        execute_home_edge=lambda req: valid_receipt(exit_code=1),
        verify_postcondition=lambda *_: True,
    )
    assert result.status == "execution_failed"
    assert not result.verified
