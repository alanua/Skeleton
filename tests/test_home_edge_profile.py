from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.home_edge.profile import load_home_edge_profile, synthetic_profile_mapping


HOME_EDGE_PROFILE_ENV = (
    "SKELETON_HOME_EDGE_01_PROFILE",
    "SKELETON_HOME_EDGE_01_HOSTNAME",
    "SKELETON_HOME_EDGE_01_TAILSCALE_IP",
    "SKELETON_HOME_EDGE_01_CONTROLLER_HOST",
    "SKELETON_HOME_EDGE_01_CONTROLLER_TAILSCALE_IP",
    "SKELETON_HOME_EDGE_01_TARGET_USER",
)


def _clear_home_edge_profile_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in HOME_EDGE_PROFILE_ENV:
        monkeypatch.delenv(key, raising=False)


def test_home_edge_profile_registers_universal_fixed_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_home_edge_profile_env(monkeypatch)

    profile = load_home_edge_profile()

    assert profile.node_id == "home-edge-01"
    assert profile.hostname == "synthetic-home-edge"
    assert profile.tailscale_ip == "100.64.0.10"
    assert profile.target_user == "home-edge-runner"
    assert profile.transport == "openssh_over_tailscale_ip"
    assert profile.identity_env == "SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE"
    assert profile.known_hosts_env == "SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE"
    assert profile.task_model == "typed_allowlisted_actions"
    assert "browser_and_desktop_diagnostics" in profile.capabilities
    assert "home_automation" in profile.capabilities
    assert profile.primary_network["interface"] == "synthetic-lan"
    assert profile.is_template_identity


def test_profile_rejects_changed_target_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_home_edge_profile_env(monkeypatch)
    data = synthetic_profile_mapping()
    data["ssh"]["transport"] = "raw_shell"
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="fixed contract mismatch"):
        load_home_edge_profile(path)


def test_local_profile_file_loads_runtime_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_home_edge_profile_env(monkeypatch)
    data = synthetic_profile_mapping()
    data["hostname"] = "runtime-host"
    data["tailscale_ip"] = "100.64.10.74"
    data["controller"]["host"] = "runtime-controller"
    data["controller"]["tailscale_ip"] = "100.64.10.63"
    data["ssh"]["target_user"] = "runtime-user"
    data["primary_network"] = {"interface": "test-lan0", "gateway": "192.0.2.254"}
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    profile = load_home_edge_profile(path)

    assert profile.hostname == "runtime-host"
    assert profile.tailscale_ip == "100.64.10.74"
    assert profile.target_user == "runtime-user"
    assert not profile.is_template_identity


def test_environment_overrides_create_runtime_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_HOSTNAME", "runtime-host")
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_TAILSCALE_IP", "100.64.10.74")
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_TARGET_USER", "runtime-user")

    profile = load_home_edge_profile()

    assert profile.hostname == "runtime-host"
    assert profile.tailscale_ip == "100.64.10.74"
    assert profile.target_user == "runtime-user"
    assert not profile.is_template_identity
