from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Mapping

import pytest

from core.home_edge import display_power_off as display
from core.home_edge.executor import HomeEdgeExecReceipt, HomeEdgeExecRequest, sign_request
from core.home_edge.executor_gateway import EXEC_HMAC_SECRET_ENV


SHA = "a" * 40
SECRET = "test-home-edge-secret"


def issue_body(**updates: str) -> str:
    fields = {
        "Mode": "RUNTIME_MAINTENANCE_TASK",
        "Maintenance Task ID": display.TASK_ID,
        "Operator Approval": display.OPERATOR_APPROVAL,
        "Target Node": display.TARGET_NODE,
        "Privacy Boundary": display.PRIVACY_BOUNDARY,
    }
    fields.update(updates)
    return "\n".join(f"{key}: {value}" for key, value in fields.items())


def public_receipt(**updates: object) -> dict[str, object]:
    receipt: dict[str, object] = {
        "maintenance_task_id": display.TASK_ID,
        "request_accepted": True,
        "applied": True,
        "physically_verified": True,
        "display_power_state": "off",
        "executor_receipt_hash": "pending",
        "stable_reason": "completed",
        "success_criteria": "met",
    }
    receipt.update(updates)
    return receipt


def executor_receipt(stdout: str) -> HomeEdgeExecReceipt:
    now = datetime.now(UTC).isoformat()
    return HomeEdgeExecReceipt(
        status="ok",
        request_id="req",
        node_id=display.TARGET_NODE,
        execution_lane=display.EXECUTION_LANE,
        exit_code=0,
        stdout=stdout,
        stderr="",
        started_at=now,
        finished_at=now,
        duration_seconds=0.01,
        idempotency="executed",
        receipt_hash="f" * 64,
    )


def test_literal_current_2374_metadata_reaches_signer_and_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signer_calls: list[Mapping[str, str]] = []
    transport_calls: list[Mapping[str, Any]] = []
    signed = display.build_signed_display_off_request(
        environment={EXEC_HMAC_SECRET_ENV: SECRET}
    ).to_mapping()

    def fake_signer(metadata: Mapping[str, str]) -> Mapping[str, Any]:
        signer_calls.append(metadata)
        return signed

    def fake_execute(request: Mapping[str, Any]) -> HomeEdgeExecReceipt:
        transport_calls.append(request)
        return executor_receipt(json.dumps({"public": public_receipt()}))

    monkeypatch.setattr(display, "execute_home_edge_request", fake_execute)

    receipt = display.execute_display_power_off_task(
        issue_body(),
        registered_clean_main_sha=SHA,
        github_main_sha=SHA,
        signer=fake_signer,
    )

    assert receipt["success_criteria"] == "met"
    assert signer_calls == [display.SIGNER_STDIN]
    assert transport_calls == [signed]
    parsed = HomeEdgeExecRequest.from_mapping(transport_calls[0])
    assert parsed.signature == sign_request(parsed, SECRET)
    assert parsed.node_id == display.TARGET_NODE
    assert parsed.operator_approval_ref == display.OPERATOR_APPROVAL
    assert parsed.execution_lane.value == "routine_mutation"
    assert parsed.run_as.value == "desktop-user"
    assert parsed.script == display.DISPLAY_POWER_OFF_SCRIPT


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"Privacy Boundary": "PRIVATE_RUNTIME_STATE_PUBLIC_SAFE_STATUS "}, "privacy_boundary_mismatch"),
        ({"Privacy Boundary": "PRIVATE_CONTROLLER_CREDENTIAL"}, "privacy_boundary_mismatch"),
        ({"Maintenance Task ID": display.TASK_ID + "_extra"}, "maintenance_task_id_mismatch"),
        ({"Operator Approval": display.OPERATOR_APPROVAL + "_EXTRA"}, "operator_approval_mismatch"),
        ({"Target Node": "home-edge-02"}, "target_node_mismatch"),
    ],
)
def test_altered_authority_fields_fail_before_signer_or_transport(
    monkeypatch: pytest.MonkeyPatch,
    updates: dict[str, str],
    reason: str,
) -> None:
    signer_calls = 0
    transport_calls = 0

    def fake_signer(_metadata: Mapping[str, str]) -> Mapping[str, Any]:
        nonlocal signer_calls
        signer_calls += 1
        return {}

    def fake_execute(_request: Mapping[str, Any]) -> HomeEdgeExecReceipt:
        nonlocal transport_calls
        transport_calls += 1
        return executor_receipt("")

    monkeypatch.setattr(display, "execute_home_edge_request", fake_execute)

    with pytest.raises(ValueError, match=reason):
        display.execute_display_power_off_task(
            issue_body(**updates),
            registered_clean_main_sha=SHA,
            github_main_sha=SHA,
            signer=fake_signer,
        )

    assert signer_calls == 0
    assert transport_calls == 0


def test_repository_and_expected_main_sha_are_not_required_or_allowed() -> None:
    parsed = display.parse_runtime_input(issue_body())
    assert "Repository" not in parsed
    assert "Expected Main SHA" not in parsed
    with pytest.raises(ValueError, match="unknown_runtime_input_field"):
        display.parse_runtime_input(issue_body() + "\nRepository: alanua/Skeleton")
    with pytest.raises(ValueError, match="unknown_runtime_input_field"):
        display.parse_runtime_input(issue_body() + "\nExpected Main SHA: " + SHA)


def test_success_requires_physical_verification_and_off_state() -> None:
    assert display.success_criteria_met(public_receipt()) is True
    assert display.success_criteria_met(public_receipt(physically_verified=False)) is False
    assert display.success_criteria_met(public_receipt(display_power_state="not_off")) is False
    assert display.success_criteria_met(public_receipt(applied=False)) is False


def test_trusted_main_sha_equality_checked_outside_issue_body() -> None:
    display.validate_trusted_main_sha(registered_clean_main_sha=SHA, github_main_sha=SHA)
    with pytest.raises(ValueError, match="trusted_main_sha_mismatch"):
        display.validate_trusted_main_sha(
            registered_clean_main_sha=SHA,
            github_main_sha="b" * 40,
        )
