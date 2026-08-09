from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from typing import Any, Mapping

import pytest

from core.home_edge import display_power_off as power
from core.home_edge.executor import HomeEdgeExecReceipt, HomeEdgeExecRequest, sign_request


SHA = "a" * 40
SECRET = "test-home-edge-secret"


def canonical_body(**updates: str) -> str:
    fields = {
        "Mode": "RUNTIME_MAINTENANCE_TASK",
        "Maintenance Task ID": power.TASK_ID,
        "Risk": power.RISK,
        "Target Node": power.TARGET_NODE,
        "Operator Approval": power.OPERATOR_APPROVAL,
        "Privacy Boundary": power.PRIVACY_BOUNDARY,
    }
    fields.update(updates)
    return "\n".join(f"{key}: {value}" for key, value in fields.items())


def make_signed_request() -> dict[str, object]:
    request = HomeEdgeExecRequest.from_mapping(
        {
            "request_id": f"{power.TASK_ID}-test",
            "node_id": power.TARGET_NODE,
            "execution_lane": power.EXECUTION_LANE,
            "timeout_seconds": power.REQUEST_TIMEOUT_SECONDS,
            "idempotency_key": power.IDEMPOTENCY_KEY,
            "operator_approval_ref": power.OPERATOR_APPROVAL,
            "run_as": power.RUN_AS,
            "mode": "script",
            "script": power.DISPLAY_POWER_OFF_SCRIPT,
            "script_interpreter": "bash",
            "timestamp": datetime.now(UTC).isoformat(),
            "nonce": f"{power.TASK_ID}-test",
            "max_output_bytes": power.MAX_EXECUTOR_OUTPUT_BYTES,
            "public": False,
        }
    )
    return HomeEdgeExecRequest.from_mapping(
        {
            **request.to_mapping(include_signature=False),
            "signature": sign_request(request, SECRET),
        }
    ).to_mapping()


def executor_receipt(stdout: str) -> HomeEdgeExecReceipt:
    now = datetime.now(UTC).isoformat()
    return HomeEdgeExecReceipt(
        status="ok",
        request_id="req",
        node_id=power.TARGET_NODE,
        execution_lane=power.EXECUTION_LANE,
        exit_code=0,
        stdout=stdout,
        stderr="",
        started_at=now,
        finished_at=now,
        duration_seconds=0.01,
        idempotency="executed",
        receipt_hash="f" * 64,
    )


def public_stdout(**updates: object) -> str:
    public: dict[str, object] = {
        "maintenance_task_id": power.TASK_ID,
        "request_accepted": True,
        "applied": True,
        "physically_verified": True,
        "display_power_state": "off",
        "executor_receipt_hash": "pending",
        "stable_reason": "completed",
        "success_criteria": "met",
    }
    public.update(updates)
    return json.dumps({"public": public}, sort_keys=True, separators=(",", ":"))


def test_canonical_2374_metadata_reaches_signer_and_existing_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed = make_signed_request()
    signer_calls: list[Mapping[str, str]] = []
    transport_calls: list[Mapping[str, Any]] = []
    monkeypatch.delenv("SKELETON_HOME_EDGE_EXEC_HMAC_SECRET", raising=False)
    monkeypatch.setattr(
        power,
        "execute_home_edge_request",
        lambda request: (transport_calls.append(request), executor_receipt(public_stdout()))[1],
    )

    def fake_signer(metadata: Mapping[str, str]) -> Mapping[str, Any]:
        signer_calls.append(metadata)
        return signed

    receipt = power.execute_display_power_off_task(
        canonical_body(),
        registered_clean_main_sha=SHA,
        github_main_sha=SHA,
        signer=fake_signer,
    )

    assert power.success_criteria_met(receipt)
    assert signer_calls == [power.SIGNER_STDIN]
    assert transport_calls == [signed]
    assert os.environ.get("SKELETON_HOME_EDGE_EXEC_HMAC_SECRET") is None


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"Target Node": "home-edge-0l"}, "target_node_mismatch"),
        ({"Maintenance Task ID": "home_edge_01_display_power_off"}, "maintenance_task_id_mismatch"),
        ({"Operator Approval": "APPROVED"}, "operator_approval_mismatch"),
        ({"Repository": "alanua/Skeleton"}, "unknown_runtime_input_field"),
        ({"Expected Main SHA": SHA}, "unknown_runtime_input_field"),
        ({"Target": power.TARGET_NODE}, "unknown_runtime_input_field"),
    ],
)
def test_malformed_authority_fields_fail_before_signer_or_transport(
    monkeypatch: pytest.MonkeyPatch,
    updates: dict[str, str],
    reason: str,
) -> None:
    monkeypatch.setattr(power, "invoke_fixed_display_off_signer", lambda _: pytest.fail("signer must not run"))
    monkeypatch.setattr(power, "execute_home_edge_request", lambda _: pytest.fail("transport must not run"))
    with pytest.raises(ValueError, match=reason):
        power.execute_display_power_off_task(
            canonical_body(**updates),
            registered_clean_main_sha=SHA,
            github_main_sha=SHA,
        )


def test_signer_rejects_argv_and_unknown_stdin_before_secret_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        power,
        "resolve_exec_hmac_secret",
        lambda: pytest.fail("secret must not be read"),
    )
    valid = json.dumps(power.SIGNER_STDIN)
    with pytest.raises(ValueError, match="signer_argv_rejected"):
        power.signer_envelope_from_stdin(valid, argv=["--bad"])
    with pytest.raises(ValueError, match="signer_stdin_unknown_field"):
        power.signer_envelope_from_stdin(
            json.dumps({**power.SIGNER_STDIN, "argv": []}), argv=[]
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("node_id", "other-node"),
        ("execution_lane", "destructive"),
        ("run_as", "root"),
        ("script", "echo bad"),
        ("script_interpreter", "python3"),
        ("timeout_seconds", power.REQUEST_TIMEOUT_SECONDS + 1),
        ("max_output_bytes", power.MAX_EXECUTOR_OUTPUT_BYTES + 1),
        ("idempotency_key", "other"),
        ("operator_approval_ref", "other"),
        ("public", True),
        ("environment", {"PATH": "/tmp"}),
        ("cwd", "/tmp"),
        ("stdin_text", "bad"),
    ],
)
def test_altered_signed_authority_rejected_before_transport(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    altered = make_signed_request()
    altered[field] = value
    monkeypatch.setattr(
        power,
        "execute_home_edge_request",
        lambda _: pytest.fail("transport must not run"),
    )
    with pytest.raises(ValueError):
        power.execute_display_power_off_task(
            canonical_body(),
            registered_clean_main_sha=SHA,
            github_main_sha=SHA,
            signer=lambda metadata: altered,
        )


def test_exact_sudo_command_and_minimal_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args[0]
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args[0], 0, json.dumps(make_signed_request()), "")

    monkeypatch.setattr(power.subprocess, "run", fake_run)
    power.invoke_fixed_display_off_signer(power.SIGNER_STDIN)
    assert captured["args"] == power.SIGNER_COMMAND
    assert captured["kwargs"]["env"] == power.SIGNER_ENVIRONMENT


def test_receipt_states_are_distinct_and_success_requires_physical_off() -> None:
    not_verified = power.public_receipt_from_executor_stdout(
        executor_receipt(
            public_stdout(
                physically_verified=False,
                display_power_state="not_off",
                success_criteria="not_met",
            )
        ).to_mapping()
    )
    assert not_verified["request_accepted"] is True
    assert not_verified["applied"] is True
    assert not_verified["physically_verified"] is False
    assert not power.success_criteria_met(not_verified)
