from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from typing import Any, Mapping

import pytest

from core.home_edge import display_power_off as power_off
from core.home_edge.executor import (
    HomeEdgeExecError,
    HomeEdgeExecReceipt,
    HomeEdgeExecRequest,
    sign_request,
)


SHA = "a" * 40
SECRET = "test-home-edge-secret"


def issue_body(**updates: str) -> str:
    fields = {
        "Mode": "RUNTIME_MAINTENANCE_TASK",
        "Maintenance Task ID": power_off.TASK_ID,
        "Repository": power_off.REPOSITORY,
        "Expected Main SHA": SHA,
        "Operator Approval": power_off.OPERATOR_APPROVAL,
        "Target Node": power_off.TARGET_NODE,
    }
    fields.update(updates)
    return "\n".join(f"{key}: {value}" for key, value in fields.items())


def public_receipt(**updates: object) -> dict[str, object]:
    receipt: dict[str, object] = {
        "maintenance_task_id": power_off.TASK_ID,
        "operation_id": power_off.OPERATION_ID,
        "request_accepted": True,
        "applied": True,
        "physically_verified": "unobservable",
        "mutation_executor_receipt_hash": "pending",
        "audit_receipt_ref": "home_edge_exec_audit",
        "audit_receipt_hash": "0" * 64,
        "stable_reason": "applied_not_physically_observable",
        "success_criteria": "met",
    }
    receipt.update(updates)
    return receipt


def executor_receipt(
    stdout: str,
    *,
    exit_code: int = 0,
    status: str = "ok",
) -> HomeEdgeExecReceipt:
    now = datetime.now(UTC).isoformat()
    return HomeEdgeExecReceipt(
        status=status,
        request_id="req",
        node_id=power_off.TARGET_NODE,
        execution_lane="routine_mutation",
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
        started_at=now,
        finished_at=now,
        duration_seconds=0.01,
        idempotency="executed",
        receipt_hash="f" * 64,
    )


def assert_no_private_transport_leak(receipt: Mapping[str, object]) -> None:
    encoded = json.dumps(receipt, sort_keys=True)
    assert "/private/id_ed25519" not in encoded
    assert "home-edge-01.tail" not in encoded
    assert "super-secret-token" not in encoded
    assert "TimeoutExpired" not in encoded
    assert "RuntimeError" not in encoded
    assert "HomeEdgeExecError" not in encoded


def test_exact_runtime_input_accepted_and_main_sha_checked() -> None:
    parsed = power_off.parse_runtime_input(issue_body())

    assert parsed.repository == power_off.REPOSITORY
    assert parsed.expected_main_sha == SHA
    assert parsed.operator_approval == power_off.OPERATOR_APPROVAL
    power_off.validate_main_sha(
        SHA,
        registered_clean_main_sha=SHA,
        github_main_sha=SHA,
    )


@pytest.mark.parametrize(
    "body,reason",
    [
        (issue_body(**{"Expected Main SHA": "A" * 40}), "expected_main_sha_malformed"),
        (issue_body() + "\nExpected Main SHA: " + SHA, "duplicate_runtime_input_field"),
        (issue_body(**{"Operator Approval": ""}), "missing_operator_approval"),
        (issue_body(**{"Operator Approval": "approved"}), "operator_approval_mismatch"),
        (issue_body() + "\nCommand: xset dpms force off", "unknown_runtime_input_field"),
        (issue_body() + "\nPath: /home/oleksii/.Xauthority", "unknown_runtime_input_field"),
        (issue_body() + "\nDisplay: :0", "unknown_runtime_input_field"),
        (issue_body() + "\nTimeout: 1", "unknown_runtime_input_field"),
        (issue_body() + "\nLane: destructive", "unknown_runtime_input_field"),
        (issue_body() + "\nRun As: root", "unknown_runtime_input_field"),
    ],
)
def test_malformed_duplicate_and_behavior_changing_fields_rejected(
    body: str, reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        power_off.parse_runtime_input(body)


def test_request_is_signed_fixed_idempotent_and_dispatched_only_through_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Mapping[str, Any]] = []
    monkeypatch.setenv(power_off.EXEC_HMAC_SECRET_ENV, SECRET)

    def fake_execute(request: Mapping[str, Any]):
        calls.append(request)
        return executor_receipt(json.dumps(public_receipt()))

    monkeypatch.setattr(power_off, "execute_home_edge_request", fake_execute)

    receipt = power_off.execute_display_power_off_task(
        issue_body(),
        registered_clean_main_sha=SHA,
        github_main_sha=SHA,
    )

    assert receipt["success_criteria"] == "met"
    assert len(calls) == 1
    request = HomeEdgeExecRequest.from_mapping(calls[0])
    assert request.signature == sign_request(request, SECRET)
    assert request.node_id == power_off.TARGET_NODE
    assert request.run_as.value == "desktop-user"
    assert request.execution_lane.value == "routine_mutation"
    assert request.timeout_seconds == 90
    assert request.idempotency_key == power_off.IDEMPOTENCY_KEY
    assert request.operator_approval_ref == power_off.OPERATOR_APPROVAL
    assert request.script == power_off.DISPLAY_POWER_OFF_SCRIPT
    assert "xset dpms force off" in request.script
    assert "Monitor is Off" in request.script
    assert "systemctl reboot" not in request.script
    assert "shutdown " not in request.script
    assert "ssh -" not in request.script
    assert receipt["request_accepted"] is True
    assert receipt["applied"] is True
    assert receipt["physically_verified"] == "unobservable"
    assert receipt["mutation_executor_receipt_hash"] == "f" * 64
    assert receipt["audit_receipt_hash"] == power_off._audit_hash(receipt)


@pytest.mark.parametrize(
    "receipt,met",
    [
        (public_receipt(request_accepted=True, applied=True, physically_verified="yes"), True),
        (
            public_receipt(
                request_accepted=True,
                applied=True,
                physically_verified="unobservable",
            ),
            True,
        ),
        (public_receipt(request_accepted=True, applied=False, physically_verified="yes"), False),
        (public_receipt(request_accepted=False, applied=True, physically_verified="yes"), False),
        (public_receipt(request_accepted=True, applied=True, physically_verified="no"), False),
    ],
)
def test_public_receipt_semantics_keep_acceptance_application_and_physical_observation_distinct(
    receipt: dict[str, object], met: bool
) -> None:
    sanitized = power_off.sanitize_public_receipt(receipt)

    assert power_off.success_criteria_met(sanitized) is met
    lines = "\n".join(power_off.receipt_status_lines(sanitized))
    assert "request_accepted=" in lines
    assert "applied=" in lines
    assert "physically_verified=" in lines
    assert "/home/" not in lines
    assert "DISPLAY" not in lines


def test_execute_home_edge_exec_error_fails_closed_without_private_leakage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(power_off.EXEC_HMAC_SECRET_ENV, SECRET)

    def fake_execute(_request: Mapping[str, Any]):
        raise HomeEdgeExecError(
            "remote home_edge_exec failed: /private/id_ed25519 super-secret-token"
        )

    monkeypatch.setattr(power_off, "execute_home_edge_request", fake_execute)

    receipt = power_off.execute_display_power_off_task(
        issue_body(),
        registered_clean_main_sha=SHA,
        github_main_sha=SHA,
    )

    assert receipt["success_criteria"] == "not_met"
    assert receipt["stable_reason"] == "executor_transport_failed"
    assert_no_private_transport_leak(receipt)


def test_execute_timeout_fails_closed_without_private_leakage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(power_off.EXEC_HMAC_SECRET_ENV, SECRET)

    def fake_execute(_request: Mapping[str, Any]):
        raise subprocess.TimeoutExpired(
            cmd=["ssh", "home-edge-01.tail", "-i", "/private/id_ed25519"],
            timeout=120,
            output="super-secret-token",
        )

    monkeypatch.setattr(power_off, "execute_home_edge_request", fake_execute)

    receipt = power_off.execute_display_power_off_task(
        issue_body(),
        registered_clean_main_sha=SHA,
        github_main_sha=SHA,
    )

    assert receipt["success_criteria"] == "not_met"
    assert receipt["stable_reason"] == "executor_transport_timeout"
    assert_no_private_transport_leak(receipt)


def test_failed_executor_preserves_valid_embedded_blocked_receipt() -> None:
    embedded = public_receipt(
        request_accepted=True,
        applied=False,
        physically_verified="no",
        stable_reason="display_power_off_failed",
        success_criteria="not_met",
    )

    parsed = power_off.public_receipt_from_executor_stdout(
        executor_receipt(
            "diagnostic preface\n" + json.dumps(embedded) + "\n",
            exit_code=20,
            status="failed",
        ).to_mapping()
    )

    assert parsed["request_accepted"] is True
    assert parsed["applied"] is False
    assert parsed["physically_verified"] == "no"
    assert parsed["stable_reason"] == "display_power_off_failed"
    assert parsed["success_criteria"] == "not_met"
    assert parsed["audit_receipt_hash"] == power_off._audit_hash(parsed)
