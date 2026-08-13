from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from core.home_edge import display_power_off


HEAD_SHA = "a" * 40
EXECUTOR_RECEIPT_HASH = "b" * 64


@dataclass(frozen=True)
class FakeExecutorReceipt:
    stdout: str
    status: str = "ok"
    exit_code: int = 0
    idempotency: str = "executed"
    receipt_hash: str = EXECUTOR_RECEIPT_HASH

    def to_mapping(self) -> dict[str, object]:
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "idempotency": self.idempotency,
        }


def runtime_body(
    *,
    task_id: str = display_power_off.TASK_ID,
    approval: str = display_power_off.OPERATOR_APPROVAL,
    expected_sha: str = HEAD_SHA,
) -> str:
    return "\n".join(
        (
            "Mode: RUNTIME_MAINTENANCE_TASK",
            f"Maintenance Task ID: {task_id}",
            f"Repository: {display_power_off.REPOSITORY}",
            f"Expected Main SHA: {expected_sha}",
            f"Operator Approval: {approval}",
            f"Target: {display_power_off.TARGET_NODE}",
        )
    )


def public_receipt(**overrides: object) -> dict[str, object]:
    receipt: dict[str, object] = {
        "maintenance_task_id": display_power_off.TASK_ID,
        "request_accepted": True,
        "applied": True,
        "physically_verified": True,
        "display_power_status": "off",
        "idempotency_status": "pending",
        "executor_receipt_hash": "pending",
        "audit_receipt_hash": "pending",
        "stable_reason": "completed",
        "success_criteria": "met",
    }
    receipt.update(overrides)
    return receipt


def test_parse_runtime_input_requires_exact_operator_approval() -> None:
    parsed = display_power_off.parse_runtime_input(runtime_body())

    assert parsed.operator_approval == display_power_off.OPERATOR_APPROVAL
    with pytest.raises(ValueError, match="operator_approval_mismatch"):
        display_power_off.parse_runtime_input(runtime_body(approval="EXPLICIT_TURN_OFF"))


def test_parse_runtime_input_rejects_near_miss_task_id() -> None:
    with pytest.raises(ValueError, match="maintenance_task_id_mismatch"):
        display_power_off.parse_runtime_input(
            runtime_body(task_id="home_edge_01_display_power_off_v1_extra")
        )


def test_build_request_uses_audited_executor_path_and_fixed_idempotency() -> None:
    request = display_power_off.build_display_power_off_request(
        environment={"SKELETON_HOME_EDGE_EXEC_HMAC_SECRET": "test-secret"}
    )
    payload = request.to_mapping()

    assert payload["node_id"] == "home-edge-01"
    assert payload["execution_lane"] == "routine_mutation"
    assert payload["operator_approval_ref"] == display_power_off.OPERATOR_APPROVAL
    assert payload["idempotency_key"] == display_power_off.IDEMPOTENCY_KEY
    assert payload["run_as"] == "desktop-user"
    assert payload["mode"] == "script"
    assert "xset dpms force off" in str(payload["script"])
    assert str(payload["signature"]).startswith("sha256=")


def test_execute_task_preserves_distinct_receipt_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_execute(request: dict[str, object]) -> FakeExecutorReceipt:
        captured.update(request)
        return FakeExecutorReceipt(json.dumps(public_receipt()))

    monkeypatch.setattr(display_power_off, "execute_home_edge_request", fake_execute)

    receipt = display_power_off.execute_display_power_off_task(
        runtime_body(),
        registered_clean_main_sha=HEAD_SHA,
        github_main_sha=HEAD_SHA,
        environment={"SKELETON_HOME_EDGE_EXEC_HMAC_SECRET": "test-secret"},
    )

    assert captured["idempotency_key"] == display_power_off.IDEMPOTENCY_KEY
    assert receipt["request_accepted"] is True
    assert receipt["applied"] is True
    assert receipt["physically_verified"] is True
    assert receipt["display_power_status"] == "off"
    assert receipt["idempotency_status"] == "executed"
    assert receipt["executor_receipt_hash"] == EXECUTOR_RECEIPT_HASH
    assert display_power_off.success_criteria_met(receipt)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("request_accepted", False),
        ("physically_verified", False),
        ("display_power_status", "on"),
    ),
)
def test_success_requires_request_acceptance_off_status_and_physical_verification(
    field: str, value: object
) -> None:
    receipt = public_receipt(**{field: value})

    assert not display_power_off.success_criteria_met(receipt)


def test_failed_executor_embedded_receipt_keeps_accept_apply_verify_independent() -> None:
    receipt = display_power_off.public_receipt_from_executor_stdout(
        {
            "status": "failed",
            "exit_code": 10,
            "stdout": json.dumps(
                public_receipt(
                    request_accepted=True,
                    applied=True,
                    physically_verified=False,
                    display_power_status="unverified",
                    stable_reason="physical_verification_failed",
                )
            ),
        }
    )

    assert receipt["request_accepted"] is True
    assert receipt["applied"] is True
    assert receipt["physically_verified"] is False
    assert receipt["success_criteria"] == "not_met"
