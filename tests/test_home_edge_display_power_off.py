from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import pytest

from core.home_edge.controller_auth import (
    AUTHORITY_OPERATOR_APPROVAL,
    DISPLAY_POWER_OFF_TASK_ID,
    literal_display_off_authority,
)
from core.home_edge.display_power_off import (
    DISPLAY_OFF_IDEMPOTENCY_KEY,
    DISPLAY_OFF_MAX_OUTPUT_BYTES,
    DISPLAY_OFF_REQUEST_ID,
    DISPLAY_OFF_SCRIPT,
    DISPLAY_OFF_SCRIPT_INTERPRETER,
    DISPLAY_OFF_TIMEOUT_SECONDS,
    FIXED_SIGNER_PATH,
    SIGNED_ENVELOPE_SCHEMA,
    build_display_off_request,
    execute_display_power_off_task,
    invoke_fixed_controller_signer,
)
from core.home_edge.executor import HomeEdgeExecRequest, sign_request
from core.home_edge.executor_gateway import EXEC_HMAC_SECRET_ENV


SECRET = "display-off-private-secret"


def authority_body() -> str:
    return "\n".join(
        (
            "Mode: RUNTIME_MAINTENANCE_TASK",
            f"Maintenance Task ID: {DISPLAY_POWER_OFF_TASK_ID}",
            "Risk: yellow",
            "Target Node: home-edge-01",
            f"Operator Approval: {AUTHORITY_OPERATOR_APPROVAL}",
            "Privacy Boundary: PRIVATE_RUNTIME_STATE_PUBLIC_SAFE_STATUS",
        )
    )


def signed_envelope(*, stdout: str) -> dict[str, object]:
    request = build_display_off_request(
        timestamp=datetime.now(UTC).isoformat(),
        nonce="display-off-test-nonce",
    )
    request["signature"] = sign_request(HomeEdgeExecRequest.from_mapping(request), SECRET)
    return {
        "schema": SIGNED_ENVELOPE_SCHEMA,
        "authority": literal_display_off_authority().to_mapping(),
        "request": request,
    }


class FakeTransport:
    adapter_name = "fake"

    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.requests: list[dict[str, object]] = []

    def execute(self, request, *, timeout_seconds: int):
        self.requests.append(dict(request))
        return {
            "schema": "skeleton.home_edge.exec_receipt.v1",
            "status": "ok",
            "request_id": request["request_id"],
            "node_id": request["node_id"],
            "execution_lane": request["execution_lane"],
            "exit_code": 0,
            "stdout": self.stdout,
            "stderr": "",
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "duration_seconds": 0.01,
            "idempotency": "executed",
            "receipt_hash": "hash",
            "public": True,
        }


def test_literal_2374_authority_reaches_fixed_signer_and_transport_path() -> None:
    calls: list[object] = []
    transport = FakeTransport(
        "\n".join(
            (
                "SKELETON_DISPLAY_OFF_REQUEST_ACCEPTED=true",
                "SKELETON_DISPLAY_OFF_APPLIED=true",
                "SKELETON_DISPLAY_OFF_OBSERVABLE=true",
                "SKELETON_DISPLAY_OFF_STATE=off",
            )
        )
    )

    def signer(authority):
        calls.append(authority)
        return signed_envelope(stdout=transport.stdout)

    receipt = execute_display_power_off_task(authority_body(), signer_invoker=signer, transport=transport)

    assert calls == [literal_display_off_authority()]
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request["request_id"] == DISPLAY_OFF_REQUEST_ID
    assert request["node_id"] == "home-edge-01"
    assert request["execution_lane"] == "privileged_mutation"
    assert request["run_as"] == "root"
    assert request["mode"] == "script"
    assert request["script"] == DISPLAY_OFF_SCRIPT
    assert request["script_interpreter"] == DISPLAY_OFF_SCRIPT_INTERPRETER
    assert request["timeout_seconds"] == DISPLAY_OFF_TIMEOUT_SECONDS
    assert request["max_output_bytes"] == DISPLAY_OFF_MAX_OUTPUT_BYTES
    assert request["public"] is True
    assert request["operator_approval_ref"] == AUTHORITY_OPERATOR_APPROVAL
    assert request["idempotency_key"] == DISPLAY_OFF_IDEMPOTENCY_KEY
    assert receipt.request_accepted is True
    assert receipt.applied is True
    assert receipt.physically_verified is True
    assert receipt.success_criteria == "met"


@pytest.mark.parametrize(
    ("needle", "replacement"),
    (
        ("RUNTIME_MAINTENANCE_TASK", "RUNTIME_TASK"),
        (DISPLAY_POWER_OFF_TASK_ID, "home_edge_01_display_power_on_v1"),
        ("Risk: yellow", "Risk: red"),
        ("Target Node: home-edge-01", "Target Node: home-edge-02"),
        (AUTHORITY_OPERATOR_APPROVAL, "EXPLICIT_2026_08_09_TURN_ON_HOME_EDGE_MONITOR"),
        ("PRIVATE_RUNTIME_STATE_PUBLIC_SAFE_STATUS", "PRIVATE_RUNTIME_STATE"),
    ),
)
def test_authority_near_miss_rejects_before_signer_or_transport(needle: str, replacement: str) -> None:
    signer = mock.Mock()
    transport = FakeTransport("")

    with pytest.raises(ValueError):
        execute_display_power_off_task(
            authority_body().replace(needle, replacement, 1),
            signer_invoker=signer,
            transport=transport,
        )

    signer.assert_not_called()
    assert transport.requests == []


def test_runner_live_path_does_not_read_hmac_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXEC_HMAC_SECRET_ENV, SECRET)

    def deny_get(name: str, default: object = None) -> object:
        if name == EXEC_HMAC_SECRET_ENV:
            raise AssertionError("runner attempted to read HMAC")
        return default

    completed = subprocess.CompletedProcess(
        args=["/usr/bin/sudo"],
        returncode=0,
        stdout=json.dumps(signed_envelope(stdout="")) + "\n",
        stderr="",
    )
    with mock.patch.object(os.environ, "get", side_effect=deny_get), mock.patch(
        "core.home_edge.display_power_off.subprocess.run",
        return_value=completed,
    ) as run:
        envelope = invoke_fixed_controller_signer(literal_display_off_authority())

    assert envelope["schema"] == SIGNED_ENVELOPE_SCHEMA
    args = run.call_args.args[0]
    kwargs = run.call_args.kwargs
    assert args == ["/usr/bin/sudo", "--non-interactive", "--", str(FIXED_SIGNER_PATH)]
    assert EXEC_HMAC_SECRET_ENV not in kwargs["env"]


def test_altered_signed_envelope_rejects_before_transport() -> None:
    envelope = signed_envelope(stdout="")
    request = dict(envelope["request"])
    request["timeout_seconds"] = DISPLAY_OFF_TIMEOUT_SECONDS + 1
    envelope["request"] = request
    transport = FakeTransport("")

    with pytest.raises(ValueError):
        execute_display_power_off_task(authority_body(), signer_invoker=lambda _authority: envelope, transport=transport)

    assert transport.requests == []


def test_exit_zero_without_independent_observation_is_not_verified() -> None:
    transport = FakeTransport(
        "\n".join(
            (
                "SKELETON_DISPLAY_OFF_REQUEST_ACCEPTED=true",
                "SKELETON_DISPLAY_OFF_APPLIED=true",
                "SKELETON_DISPLAY_OFF_OBSERVABLE=false",
                "SKELETON_DISPLAY_OFF_STATE=unknown",
            )
        )
    )

    receipt = execute_display_power_off_task(
        authority_body(),
        signer_invoker=lambda _authority: signed_envelope(stdout=transport.stdout),
        transport=transport,
    )

    assert receipt.request_accepted is True
    assert receipt.applied is True
    assert receipt.physically_verified is False
    assert receipt.physical_verification == "unobservable"
    assert receipt.success_criteria == "not_met"


def test_independent_observed_off_state_is_verified() -> None:
    transport = FakeTransport(
        "\n".join(
            (
                "SKELETON_DISPLAY_OFF_REQUEST_ACCEPTED=true",
                "SKELETON_DISPLAY_OFF_APPLIED=true",
                "SKELETON_DISPLAY_OFF_OBSERVABLE=true",
                "SKELETON_DISPLAY_OFF_STATE=off",
            )
        )
    )

    receipt = execute_display_power_off_task(
        authority_body(),
        signer_invoker=lambda _authority: signed_envelope(stdout=transport.stdout),
        transport=transport,
    )

    assert receipt.physically_verified is True
    assert receipt.physical_verification == "met"
    assert receipt.success_criteria == "met"


def test_installed_signer_survives_repository_removed_and_checkout_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = tmp_path / "root/usr/local/libexec/skeleton/home-edge-display-off-controller-signer/current"
    installed.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "scripts/home_edge_display_power_off_signer.py"
    checkout_copy = tmp_path / "checkout/scripts/home_edge_display_power_off_signer.py"
    checkout_copy.parent.mkdir(parents=True)
    shutil.copy2(source, checkout_copy)
    target = installed / "home_edge_display_power_off_signer.py"
    shutil.copy2(checkout_copy, target)
    target.chmod(0o755)
    checkout_copy.write_text(checkout_copy.read_text(encoding="utf-8") + "\n# checkout mutation\n", encoding="utf-8")
    shutil.rmtree(checkout_copy.parents[1])

    monkeypatch.setenv(EXEC_HMAC_SECRET_ENV, SECRET)
    completed = subprocess.run(
        [sys.executable, str(target)],
        input=json.dumps({"authority": literal_display_off_authority().to_mapping()}),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    envelope = json.loads(completed.stdout)
    assert envelope["schema"] == SIGNED_ENVELOPE_SCHEMA
    assert envelope["request"]["script"] == DISPLAY_OFF_SCRIPT
