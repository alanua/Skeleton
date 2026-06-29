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
    assert '"connection.autoconnect",' in payload
    assert '"ipv4.never-default",' in payload
    assert '"ipv6.never-default",' in payload


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
