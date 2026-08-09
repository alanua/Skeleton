from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import pytest

from core.home_edge import display_power_off as power
from core.home_edge.executor import HomeEdgeExecReceipt, HomeEdgeExecRequest, sign_request


SHA = "a" * 40
SECRET = "test-home-edge-secret"


def issue_body(**updates: str) -> str:
    fields = {
        "Mode": "RUNTIME_MAINTENANCE_TASK",
        "Maintenance Task ID": power.TASK_ID,
        "Repository": power.REPOSITORY,
        "Expected Main SHA": SHA,
        "Operator Approval": power.OPERATOR_APPROVAL,
        "Target": power.TARGET_NODE,
    }
    fields.update(updates)
    return "\n".join(f"{key}: {value}" for key, value in fields.items())


def signed_request(**updates: object) -> dict[str, object]:
    request = power.build_signed_display_off_request(
        environment={power.EXEC_HMAC_SECRET_ENV: SECRET}
    ).to_mapping()
    request.update(updates)
    return request


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


def test_unprivileged_runner_obtains_signed_fixed_request_and_executes_existing_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Mapping[str, Any]] = []
    signed = signed_request()

    def fake_execute(request: Mapping[str, Any]) -> HomeEdgeExecReceipt:
        calls.append(request)
        return executor_receipt(public_stdout())

    monkeypatch.delenv("SKELETON_HOME_EDGE_EXEC_HMAC_SECRET", raising=False)
    monkeypatch.setattr(power, "execute_home_edge_request", fake_execute)

    receipt = power.execute_display_power_off_task(
        issue_body(),
        registered_clean_main_sha=SHA,
        github_main_sha=SHA,
        signer=lambda metadata: signed,
    )

    assert power.success_criteria_met(receipt)
    assert calls == [signed]
    assert os.environ.get("SKELETON_HOME_EDGE_EXEC_HMAC_SECRET") is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("node_id", "other-node"),
        ("execution_lane", "destructive"),
        ("run_as", "root"),
        ("script", "echo unsafe"),
        ("script_interpreter", "python3"),
        ("timeout_seconds", power.REQUEST_TIMEOUT_SECONDS + 1),
        ("max_output_bytes", power.MAX_EXECUTOR_OUTPUT_BYTES + 1),
        ("idempotency_key", "other-key"),
        ("operator_approval_ref", "other-approval"),
        ("public", True),
        ("environment", {"PATH": "/tmp"}),
        ("cwd", "/tmp"),
        ("stdin_text", "unsafe"),
    ],
)
def test_altered_signed_request_authority_fields_rejected_before_transport(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    altered = signed_request(**{field: value})
    monkeypatch.setattr(
        power,
        "execute_home_edge_request",
        lambda _request: pytest.fail("transport must not run"),
    )

    with pytest.raises(ValueError):
        power.execute_display_power_off_task(
            issue_body(),
            registered_clean_main_sha=SHA,
            github_main_sha=SHA,
            signer=lambda metadata: altered,
        )


def test_signer_rejects_argv_and_arbitrary_stdin_before_hmac_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        power,
        "_resolve_exec_hmac_secret",
        lambda **_kwargs: pytest.fail("HMAC must not be resolved"),
    )
    valid = json.dumps(power.SIGNER_STDIN)
    with pytest.raises(ValueError, match="signer_argv_rejected"):
        power.signer_envelope_from_stdin(valid, argv=["--anything"])
    with pytest.raises(ValueError, match="signer_stdin_unknown_field"):
        power.signer_envelope_from_stdin(
            json.dumps({**power.SIGNER_STDIN, "argv": ["/bin/sh"]}),
            argv=[],
        )
    with pytest.raises(ValueError, match="signer_stdin_metadata_mismatch"):
        power.signer_envelope_from_stdin(
            json.dumps({**power.SIGNER_STDIN, "operator_approval_ref": "wrong"}),
            argv=[],
        )


def test_signer_returns_only_signed_envelope_and_never_executes_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SKELETON_HOME_EDGE_EXEC_HMAC_SECRET", SECRET)
    monkeypatch.setattr(
        power,
        "execute_home_edge_request",
        lambda _request: pytest.fail("signer must not execute transport"),
    )

    envelope = power.signer_envelope_from_stdin(json.dumps(power.SIGNER_STDIN), argv=[])
    serialized = json.dumps(envelope, sort_keys=True)
    parsed = HomeEdgeExecRequest.from_mapping(envelope)

    assert parsed.signature == sign_request(parsed, SECRET)
    assert SECRET not in serialized
    assert parsed.script == power.DISPLAY_POWER_OFF_SCRIPT


def test_exact_sudo_invocation_and_minimal_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args[0]
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args[0], 0, json.dumps(signed_request()), "")

    monkeypatch.setattr(power.subprocess, "run", fake_run)

    envelope = power.invoke_fixed_display_off_signer(power.SIGNER_STDIN)

    assert envelope["node_id"] == power.TARGET_NODE
    assert captured["args"] == power.SIGNER_COMMAND
    assert captured["kwargs"]["env"] == power.SIGNER_ENVIRONMENT
    assert json.loads(captured["kwargs"]["input"]) == power.SIGNER_STDIN


def test_request_accepted_applied_and_physically_verified_remain_distinct() -> None:
    not_verified = power.public_receipt_from_executor_stdout(
        executor_receipt(public_stdout(physically_verified=False, display_power_state="not_off", success_criteria="not_met")).to_mapping()
    )
    not_applied = power.public_receipt_from_executor_stdout(
        executor_receipt(public_stdout(applied=False, physically_verified=False, display_power_state="unknown", success_criteria="not_met")).to_mapping()
    )

    assert not_verified["request_accepted"] is True
    assert not_verified["applied"] is True
    assert not_verified["physically_verified"] is False
    assert not power.success_criteria_met(not_verified)
    assert not_applied["request_accepted"] is True
    assert not_applied["applied"] is False
    assert not_applied["physically_verified"] is False
    assert not power.success_criteria_met(not_applied)
