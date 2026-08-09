from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import pytest

from core.home_edge import display_power_off as display
from core.home_edge.executor import HomeEdgeExecReceipt, HomeEdgeExecRequest, sign_request


SHA = "b" * 40
SECRET = "display-off-test-secret"


def issue_body(**updates: str) -> str:
    fields = {
        "Mode": "RUNTIME_MAINTENANCE_TASK",
        "Maintenance Task ID": display.TASK_ID,
        "Repository": display.REPOSITORY,
        "Expected Main SHA": SHA,
        "Target": display.TARGET_NODE,
    }
    fields.update(updates)
    return "\n".join(f"{key}: {value}" for key, value in fields.items())


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
        receipt_hash="e" * 64,
    )


def node_stdout(
    *,
    accepted: bool = True,
    applied: bool = True,
    physically_verified: bool = True,
    display_state: str = "off",
) -> str:
    return json.dumps(
        {
            "public": {
                "maintenance_task_id": display.TASK_ID,
                "request_accepted": accepted,
                "applied": applied,
                "physically_verified": physically_verified,
                "display_state": display_state,
                "executor_receipt_hash": "pending",
                "stable_reason": "completed" if physically_verified else "unobservable",
                "success_criteria": "met" if accepted and applied and physically_verified and display_state == "off" else "not_met",
            }
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def signed_request(secret: str = SECRET) -> dict[str, Any]:
    request = display.build_unsigned_display_power_off_request()
    return {**request.to_mapping(include_signature=False), "signature": sign_request(request, secret)}


def test_unprivileged_runner_without_hmac_gets_fixed_signed_request_and_executes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SKELETON_HOME_EDGE_EXEC_HMAC_SECRET", raising=False)
    run_calls: list[dict[str, Any]] = []
    execute_calls: list[Mapping[str, Any]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        run_calls.append({"command": command, **kwargs})
        assert tuple(command) == display.SUDO_INVOKE
        assert kwargs["env"].get("SKELETON_HOME_EDGE_EXEC_HMAC_SECRET") is None
        return subprocess.CompletedProcess(command, 0, json.dumps(signed_request()), "")

    def fake_execute(request: Mapping[str, Any]) -> HomeEdgeExecReceipt:
        execute_calls.append(request)
        return executor_receipt(node_stdout())

    monkeypatch.setattr(display.subprocess, "run", fake_run)
    monkeypatch.setattr(display, "execute_home_edge_request", fake_execute)

    receipt = display.execute_display_power_off_task(
        issue_body(),
        registered_clean_main_sha=SHA,
        github_main_sha=SHA,
    )

    assert display.success_criteria_met(receipt)
    assert len(run_calls) == 1
    assert len(execute_calls) == 1
    request = HomeEdgeExecRequest.from_mapping(execute_calls[0])
    assert request.node_id == display.TARGET_NODE
    assert request.execution_lane.value == display.EXECUTION_LANE
    assert request.run_as.value == display.RUN_AS
    assert request.operator_approval_ref == display.OPERATOR_APPROVAL_REF
    assert request.idempotency_key == display.IDEMPOTENCY_KEY


def test_signer_rejects_argv_and_bad_stdin_before_credential_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_secret() -> str:
        raise AssertionError("credential must not be read")

    monkeypatch.setattr(display, "read_controller_exec_hmac_secret", fail_secret)

    with pytest.raises(ValueError, match="signer_argv_rejected"):
        display.build_signed_display_power_off_request_from_signer_input(argv=["--x"], stdin="{}")
    with pytest.raises(ValueError, match="signer_stdin_rejected"):
        display.build_signed_display_power_off_request_from_signer_input(
            argv=[],
            stdin=json.dumps({"schema": display.SIGNER_STDIN_SCHEMA, "task_id": display.TASK_ID, "extra": "x"}),
        )


def test_controller_signer_signs_but_never_executes_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(display, "read_controller_exec_hmac_secret", lambda: SECRET)
    monkeypatch.setattr(
        display,
        "execute_home_edge_request",
        lambda _request: (_ for _ in ()).throw(AssertionError("signer must not execute transport")),
    )

    payload = {
        "schema": display.SIGNER_STDIN_SCHEMA,
        "task_id": display.TASK_ID,
        "target_node": display.TARGET_NODE,
        "operator_approval_ref": display.OPERATOR_APPROVAL_REF,
        "idempotency_key": display.IDEMPOTENCY_KEY,
    }
    request = display.build_signed_display_power_off_request_from_signer_input(
        argv=[],
        stdin=json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )

    parsed = HomeEdgeExecRequest.from_mapping(request)
    assert parsed.signature == sign_request(parsed, SECRET)


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("node_id", "other", "signed_request_node_rejected"),
        ("execution_lane", "read_only", "signed_request_lane_rejected"),
        ("run_as", "root", "signed_request_run_as_rejected"),
        ("script", "print('changed')", "signed_request_script_rejected"),
        ("script_interpreter", "bash", "signed_request_interpreter_rejected"),
        ("timeout_seconds", 31, "signed_request_timeout_rejected"),
        ("max_output_bytes", 65000, "signed_request_output_rejected"),
        ("idempotency_key", "other", "signed_request_idempotency_rejected"),
        ("operator_approval_ref", "other", "signed_request_approval_rejected"),
        ("public", True, "signed_request_public_rejected"),
        ("environment", {"DISPLAY": ":0"}, "signed_request_environment_rejected"),
        ("cwd", "/tmp", "signed_request_cwd_rejected"),
        ("stdin_text", "x", "signed_request_stdin_rejected"),
        ("argv", ["/bin/true"], "signed_request_argv_rejected"),
    ],
)
def test_altered_signed_request_authority_fields_rejected_before_transport(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: Any,
    reason: str,
) -> None:
    request = signed_request()
    request[field] = value

    monkeypatch.setattr(display, "request_signed_display_power_off_request", lambda **_kwargs: request)
    monkeypatch.setattr(
        display,
        "execute_home_edge_request",
        lambda _request: (_ for _ in ()).throw(AssertionError("transport must not run")),
    )

    receipt = display.execute_display_power_off_task(
        issue_body(),
        registered_clean_main_sha=SHA,
        github_main_sha=SHA,
    )

    assert receipt["stable_reason"] == reason
    assert receipt["success_criteria"] == "not_met"


def test_exact_absolute_sudo_invocation_and_dedicated_sudoers_command() -> None:
    assert display.SUDO_INVOKE == (
        "/usr/bin/sudo",
        "--non-interactive",
        "--",
        "/usr/local/sbin/skeleton-home-edge-display-off-controller-signer",
    )
    sudoers = Path("scripts/skeleton-home-edge-display-off-controller-signer.sudoers").read_text(encoding="utf-8")
    assert sudoers.strip() == (
        "agent ALL=(root) NOPASSWD: "
        "/usr/local/sbin/skeleton-home-edge-display-off-controller-signer"
    )
    assert "*" not in sudoers
    assert "/bin/sh" not in sudoers
    assert "/bin/bash" not in sudoers
    assert "python" not in sudoers
    assert "SETENV" not in sudoers
    assert "ALL=(ALL)" not in sudoers


def test_receipt_semantics_keep_accepted_applied_and_physical_verification_independent() -> None:
    not_verified = display.public_receipt_from_executor_stdout(
        executor_receipt(node_stdout(accepted=True, applied=True, physically_verified=False, display_state="unknown")).to_mapping()
    )
    verified = display.public_receipt_from_executor_stdout(executor_receipt(node_stdout()).to_mapping())

    assert not_verified["request_accepted"] is True
    assert not_verified["applied"] is True
    assert not_verified["physically_verified"] is False
    assert not display.success_criteria_met(not_verified)
    assert display.success_criteria_met(verified)


def test_installer_and_wrapper_do_not_shell_load_env_files() -> None:
    installer = Path("scripts/install_home_edge_display_off_controller_signer.sh").read_text(encoding="utf-8")
    wrapper = Path("scripts/home_edge_display_off_controller_signer.py").read_text(encoding="utf-8")
    combined = installer + "\n" + wrapper

    assert "source " not in combined
    assert ". /etc/skeleton" not in combined
    assert "bash -c" not in combined
    assert "sh -c" not in combined
    assert "EnvironmentFile" not in combined


def test_node_executor_installer_and_sudoers_remain_byte_for_byte_unchanged() -> None:
    assert _sha256(Path("scripts/install_home_edge_executor.sh")) == "69d708aa513d0ca38cb0817d9894382ae6d52a99fdbd10625be6b3bc2d7c26c6"
    assert _sha256(Path("scripts/skeleton-home-edge-executor.sudoers")) == "dc4333807581d2c3e25f103c847b101d6022c201ac978ae34b3fe9aebd864d0f"


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()
