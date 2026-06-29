from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from core.home_edge.modem_action import (
    DEFAULT_SECRET_DESCRIPTOR,
    RUNTIME_APPROVAL_MARKER,
    ModemActionResult,
    compact_status,
    run_private_sim_unlock_o2_apn_test,
)
from core.home_edge.profile import load_home_edge_profile, synthetic_profile_mapping


CREATED_UUID = "11111111-2222-3333-4444-555555555555"


class FakeModemTransport:
    def __init__(self, remote_status: dict[str, str]) -> None:
        self.remote_status = remote_status
        self.payloads: list[str] = []

    def run_action(self, payload: str, *, timeout_seconds: int) -> ModemActionResult:
        self.payloads.append(payload)
        return ModemActionResult(
            state="observed",
            adapter="fake",
            stdout=json.dumps(self.remote_status),
            exit_code=0,
        )


def test_default_path_uses_strict_remote_transport_not_local_network_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = load_home_edge_profile(_private_profile_path(tmp_path))
    monkeypatch.setenv(profile.identity_env, "/tmp/home-edge-test-key")
    monkeypatch.setenv(profile.known_hosts_env, "/tmp/home-edge-test-known-hosts")
    calls: list[list[str]] = []

    def fake_run(
        command: list[str],
        *,
        input: str | None,
        stdout: object,
        stderr: object,
        text: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert command[0] == "ssh"
        assert command[-2:] == ["python3", "-"]
        assert "nmcli" not in command
        assert "ip" not in command
        assert "tailscale" not in command
        assert input is not None
        return subprocess.CompletedProcess(command, 0, json.dumps(_success_status()), "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    artifact = run_private_sim_unlock_o2_apn_test(
        runtime_approval_marker=RUNTIME_APPROVAL_MARKER,
        profile=profile,
    )

    assert artifact["status"] == "ok"
    assert len(calls) == 1


def test_modem_action_requires_separate_runtime_approval_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _fail_if_called)
    transport = FakeModemTransport(_success_status())
    artifact = run_private_sim_unlock_o2_apn_test(
        runtime_approval_marker=None,
        profile=load_home_edge_profile(_private_profile_path(tmp_path)),
        transport=transport,
    )

    assert artifact["status"] == "blocked"
    assert artifact["reason"] == "missing_runtime_approval"
    assert transport.payloads == []


def test_modem_action_blocks_invalid_private_descriptor_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _fail_if_called)
    transport = FakeModemTransport(_success_status())
    artifact = run_private_sim_unlock_o2_apn_test(
        runtime_approval_marker=RUNTIME_APPROVAL_MARKER,
        profile=load_home_edge_profile(_private_profile_path(tmp_path)),
        secret_descriptor="literal:1234",
        transport=transport,
    )

    assert artifact["status"] == "blocked"
    assert artifact["reason"] == "invalid_secret_descriptor"
    assert transport.payloads == []


def test_modem_action_blocks_synthetic_profile_before_transport() -> None:
    transport = FakeModemTransport(_success_status())
    artifact = run_private_sim_unlock_o2_apn_test(
        runtime_approval_marker=RUNTIME_APPROVAL_MARKER,
        profile=load_home_edge_profile(),
        transport=transport,
    )

    assert artifact["status"] == "blocked"
    assert artifact["reason"] == "private_runtime_profile_required"
    assert transport.payloads == []


def test_modem_action_uses_private_secret_descriptor_not_secret_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _fail_if_called)
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_MODEM_SIM_UNLOCK_PIN", "1111")
    transport = FakeModemTransport(_success_status())
    artifact = run_private_sim_unlock_o2_apn_test(
        runtime_approval_marker=RUNTIME_APPROVAL_MARKER,
        profile=load_home_edge_profile(_private_profile_path(tmp_path)),
        secret_descriptor=DEFAULT_SECRET_DESCRIPTOR,
        transport=transport,
    )

    assert artifact["status"] == "ok"
    assert compact_status(artifact)["secret_source"] == "private_inherited_descriptor"
    assert len(transport.payloads) == 1
    payload = transport.payloads[0]
    assert DEFAULT_SECRET_DESCRIPTOR in payload
    assert "1111" not in payload
    assert '"apn": "internet"' in payload
    assert '"default_route": false' in payload
    assert '"autoconnect": false' in payload


def test_remote_payload_accepts_internet_apn_and_enforces_non_default_guards(
    tmp_path: Path,
) -> None:
    payload = _payload(tmp_path)

    assert '"apn": "internet"' in payload
    assert '"internet"' in payload
    assert '"connection.autoconnect",' in payload
    assert '"no",' in payload
    assert '"ipv4.never-default",' in payload
    assert '"ipv6.never-default",' in payload
    assert "modem_apn_must_be_explicit_non_default" not in payload


def test_remote_payload_reserved_name_preflight_is_tri_state(tmp_path: Path) -> None:
    payload = _payload(tmp_path)

    assert 'return "EXISTS"' in payload
    assert 'return "NOT_FOUND"' in payload
    assert 'return "ERROR"' in payload
    assert '"reserved_profile_name_exists"' in payload
    assert '"reserved_profile_lookup_error"' in payload
    assert 'if reserved != "NOT_FOUND":' in payload


def test_remote_payload_uuid_is_generated_for_create_and_only_selector_afterward(
    tmp_path: Path,
) -> None:
    payload = _payload(tmp_path)

    assert "connection_uuid = str(uuid.uuid4())" in payload
    assert '"connection.uuid",' in payload
    assert "connection_uuid," in payload
    assert '"modify",' in payload
    assert '"uuid",' in payload
    assert '"up", "uuid", connection_uuid' in payload
    assert '"delete", "uuid", connection_uuid' in payload
    assert '"connection", "delete", CREATED_CONNECTION' not in payload
    assert '"connection", "modify", CREATED_CONNECTION' not in payload


def test_remote_payload_rolls_back_created_uuid_on_later_failures(tmp_path: Path) -> None:
    payload = _payload(tmp_path)

    for reason in (
        "connection_modify_failed",
        "connection_activation_failed",
        "active_state_validation_failed",
        "bounded_signal_probe_failed",
    ):
        assert f'"{reason}"' in payload
    assert payload.count("rollback(status, connection_uuid)") == 3
    assert "block_after_create(status, connection_uuid" in payload


def test_remote_payload_bounded_timeouts_and_inactive_state_fail_closed(
    tmp_path: Path,
) -> None:
    payload = _payload(tmp_path)

    assert "COMMAND_TIMEOUT_SECONDS = 20" in payload
    assert "except subprocess.TimeoutExpired" in payload
    assert "result is not None and result.returncode == 0" in payload
    assert '"inactive", "disconnected"' in payload
    assert "active_state_for_uuid(connection_uuid) not in CONNECTED_STATES" in payload


def test_modem_action_fail_closed_when_recovery_path_unverified(tmp_path: Path) -> None:
    remote = {
        **_success_status(),
        "route_after": "blocked",
        "status": "ok",
        "reason": "done",
    }
    artifact = run_private_sim_unlock_o2_apn_test(
        runtime_approval_marker=RUNTIME_APPROVAL_MARKER,
        profile=load_home_edge_profile(_private_profile_path(tmp_path)),
        transport=FakeModemTransport(remote),
    )

    assert artifact["status"] == "blocked"
    assert artifact["reason"] == "recovery_path_unverified"


def test_modem_action_public_status_is_allowlisted_and_aggregate_only(
    tmp_path: Path,
) -> None:
    remote = {
        **_success_status(),
        "raw_output": "private-host 1111 internet wwan0 " + CREATED_UUID,
        "reason": "done",
    }
    artifact = run_private_sim_unlock_o2_apn_test(
        runtime_approval_marker=RUNTIME_APPROVAL_MARKER,
        profile=load_home_edge_profile(_private_profile_path(tmp_path)),
        transport=FakeModemTransport(remote),
    )
    public = json.dumps(compact_status(artifact), sort_keys=True)

    assert artifact["status"] == "ok"
    for value in ("private-host", "1111", "wwan0", CREATED_UUID):
        assert value not in public


def test_modem_action_reports_rollback_on_failed_safety_check(tmp_path: Path) -> None:
    remote = {
        **_success_status(),
        "apn_profile": "blocked",
        "rollback": "rolled_back",
        "status": "blocked",
        "reason": "safety_check_failed",
    }
    artifact = run_private_sim_unlock_o2_apn_test(
        runtime_approval_marker=RUNTIME_APPROVAL_MARKER,
        profile=load_home_edge_profile(_private_profile_path(tmp_path)),
        transport=FakeModemTransport(remote),
    )

    assert artifact["status"] == "blocked"
    assert artifact["apn_profile"] == "blocked"
    assert artifact["rollback"] == "rolled_back"
    assert artifact["reason"] == "safety_check_failed"


def _payload(tmp_path: Path) -> str:
    transport = FakeModemTransport(_success_status())
    run_private_sim_unlock_o2_apn_test(
        runtime_approval_marker=RUNTIME_APPROVAL_MARKER,
        profile=load_home_edge_profile(_private_profile_path(tmp_path)),
        secret_descriptor=DEFAULT_SECRET_DESCRIPTOR,
        transport=transport,
    )
    return transport.payloads[0]


def _success_status() -> dict[str, str]:
    return {
        "approval_status": "verified",
        "route_before": "ok",
        "tailscale_before": "ok",
        "sim_unlock": "ok",
        "apn_profile": "ok",
        "connection_test": "ok",
        "rollback": "rolled_back",
        "route_after": "ok",
        "tailscale_after": "ok",
        "status": "ok",
        "reason": "done",
    }


def _private_profile_path(tmp_path: Path) -> Path:
    data = synthetic_profile_mapping()
    data["hostname"] = "runtime-host"
    data["tailscale_ip"] = "100.64.10.74"
    data["ssh"]["target_user"] = "runtime-user"
    data["primary_network"] = {"interface": "test-lan0", "gateway": "192.0.2.254"}
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _fail_if_called(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("live subprocess execution is not allowed in modem tests")
