from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Mapping

import pytest

from core.home_edge import display_power_off as display
from core.home_edge.executor import HomeEdgeExecReceipt


SHA = "a" * 40


def issue_2374_header(**updates: str) -> str:
    fields = {
        "Mode": "RUNTIME_MAINTENANCE_TASK",
        "Maintenance Task ID": "home_edge_01_display_power_off_v1",
        "Risk": "yellow",
        "Target Node": "home-edge-01",
        "Operator Approval": "EXPLICIT_2026_08_09_TURN_OFF_HOME_EDGE_MONITOR",
        "Privacy Boundary": "PRIVATE_RUNTIME_STATE_PUBLIC_SAFE_STATUS",
    }
    fields.update(updates)
    return "\n".join(f"{key}: {value}" for key, value in fields.items())


def executor_receipt(public: Mapping[str, Any], *, exit_code: int = 0, status: str = "ok") -> HomeEdgeExecReceipt:
    now = datetime.now(UTC).isoformat()
    return HomeEdgeExecReceipt(
        status=status,
        request_id="req",
        node_id=display.TARGET_NODE,
        execution_lane=display.EXECUTION_LANE,
        exit_code=exit_code,
        stdout=json.dumps({"public": public}, sort_keys=True, separators=(",", ":")),
        stderr="",
        started_at=now,
        finished_at=now,
        duration_seconds=0.01,
        idempotency="executed",
        receipt_hash="f" * 64,
    )


def public_receipt(
    *,
    request_accepted: bool = True,
    applied: bool = True,
    physically_verified: bool = False,
    physical_observation: str = "unknown",
    physical_verification_reason: str = "physical_state_unobservable",
    stable_reason: str = "physical_state_unobservable",
    success_criteria: str = "not_met",
) -> dict[str, object]:
    return {
        "maintenance_task_id": display.TASK_ID,
        "request_accepted": request_accepted,
        "applied": applied,
        "physically_verified": physically_verified,
        "physical_observation": physical_observation,
        "physical_verification_reason": physical_verification_reason,
        "executor_receipt_hash": "pending",
        "stable_reason": stable_reason,
        "success_criteria": success_criteria,
    }


def test_literal_issue_2374_header_reaches_signer_and_transport_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    original_build = display.build_display_power_off_request

    def fake_build():
        captured["signer_called"] = True
        return original_build()

    def fake_execute(request: Mapping[str, Any]) -> HomeEdgeExecReceipt:
        captured["request"] = request
        return executor_receipt(
            public_receipt(
                physically_verified=True,
                physical_observation="off",
                physical_verification_reason="dpms_monitor_off",
                stable_reason="completed",
                success_criteria="met",
            )
        )

    monkeypatch.setattr(display, "read_fixed_controller_hmac_secret", lambda: "synthetic-secret")
    monkeypatch.setattr(display, "build_display_power_off_request", fake_build)
    monkeypatch.setattr(display, "execute_home_edge_request", fake_execute)

    receipt = display.execute_display_power_off_task(
        issue_2374_header(),
        registered_clean_main_sha=SHA,
        github_main_sha=SHA,
    )

    assert captured["signer_called"] is True
    request = captured["request"]
    assert isinstance(request, Mapping)
    assert request["node_id"] == "home-edge-01"
    assert request["execution_lane"] == "routine_mutation"
    assert request["operator_approval_ref"] == display.OPERATOR_APPROVAL
    assert request["run_as"] == "desktop-user"
    assert request["mode"] == "script"
    assert request["argv"] == []
    assert receipt["request_accepted"] is True
    assert receipt["applied"] is True
    assert receipt["physically_verified"] is True
    assert display.success_criteria_met(receipt)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    (
        ("Mode", "RUNTIME_TASK", "mode_mismatch"),
        ("Maintenance Task ID", "home_edge_01_display_power_off_v2", "maintenance_task_id_mismatch"),
        ("Risk", "green", "risk_mismatch"),
        ("Target Node", "home-edge-02", "target_node_mismatch"),
        ("Operator Approval", "EXPLICIT_OTHER", "operator_approval_mismatch"),
        ("Privacy Boundary", "PRIVATE", "privacy_boundary_mismatch"),
    ),
)
def test_authority_near_misses_rejected_before_signer_or_transport(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
    reason: str,
) -> None:
    monkeypatch.setattr(
        display,
        "build_display_power_off_request",
        lambda: pytest.fail("signer must not be called"),
    )
    monkeypatch.setattr(
        display,
        "execute_home_edge_request",
        lambda _request: pytest.fail("transport must not be called"),
    )

    with pytest.raises(ValueError, match=reason):
        display.execute_display_power_off_task(
            issue_2374_header(**{field: value}),
            registered_clean_main_sha=SHA,
            github_main_sha=SHA,
        )


def test_repository_and_expected_main_sha_issue_body_authority_rejected_before_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        display,
        "execute_home_edge_request",
        lambda _request: pytest.fail("transport must not be called"),
    )

    with pytest.raises(ValueError, match="unknown_runtime_input_field"):
        display.execute_display_power_off_task(
            issue_2374_header() + "\nRepository: alanua/Skeleton\nExpected Main SHA: " + SHA,
            registered_clean_main_sha=SHA,
            github_main_sha=SHA,
        )


def test_exit_code_zero_without_independent_off_state_is_unobservable_not_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(display, "read_fixed_controller_hmac_secret", lambda: "synthetic-secret")
    monkeypatch.setattr(
        display,
        "execute_home_edge_request",
        lambda _request: executor_receipt(public_receipt(), exit_code=0, status="ok"),
    )

    receipt = display.execute_display_power_off_task(
        issue_2374_header(),
        registered_clean_main_sha=SHA,
        github_main_sha=SHA,
    )

    assert receipt["request_accepted"] is True
    assert receipt["applied"] is True
    assert receipt["physically_verified"] is False
    assert receipt["physical_observation"] == "unknown"
    assert receipt["physical_verification_reason"] == "physical_state_unobservable"
    assert receipt["success_criteria"] == "not_met"
    assert not display.success_criteria_met(receipt)


def test_independent_off_state_signal_is_physical_verification_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(display, "read_fixed_controller_hmac_secret", lambda: "synthetic-secret")
    monkeypatch.setattr(
        display,
        "execute_home_edge_request",
        lambda _request: executor_receipt(
            public_receipt(
                physically_verified=True,
                physical_observation="off",
                physical_verification_reason="dpms_monitor_off",
                stable_reason="completed",
                success_criteria="met",
            ),
            exit_code=0,
            status="ok",
        ),
    )

    receipt = display.execute_display_power_off_task(
        issue_2374_header(),
        registered_clean_main_sha=SHA,
        github_main_sha=SHA,
    )

    assert receipt["request_accepted"] is True
    assert receipt["applied"] is True
    assert receipt["physically_verified"] is True
    assert receipt["physical_observation"] == "off"
    assert receipt["success_criteria"] == "met"
    assert display.success_criteria_met(receipt)


def test_trusted_runtime_sha_equality_is_controller_context_only() -> None:
    with pytest.raises(ValueError, match="trusted_runtime_main_sha_mismatch"):
        display.execute_display_power_off_task(
            issue_2374_header(),
            registered_clean_main_sha=SHA,
            github_main_sha="b" * 40,
        )
