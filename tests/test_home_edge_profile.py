from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.home_edge.profile import LOCAL_PROFILE_ENV, load_home_edge_profile


def test_home_edge_profile_is_public_safe_template() -> None:
    profile = load_home_edge_profile(environment={})

    assert profile.node_id == "home-edge-template-01"
    assert profile.hostname == "home-edge-template-01"
    assert profile.tailscale_ip == "100.64.0.10"
    assert profile.target_user == "home-edge-user"
    assert profile.controller_host == "controller-template-01"
    assert profile.transport == "openssh_over_tailscale_ip"
    assert profile.identity_env == "SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE"
    assert profile.known_hosts_env == "SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE"
    assert profile.task_model == "typed_allowlisted_actions"
    assert "browser_and_desktop_diagnostics" in profile.capabilities
    assert profile.primary_network["interface"] == "eth-template0"


def test_profile_loads_runtime_values_from_environment() -> None:
    profile = load_home_edge_profile(
        environment={
            "SKELETON_HOME_EDGE_01_NODE_ID": "runtime-node",
            "SKELETON_HOME_EDGE_01_HOSTNAME": "runtime-host",
            "SKELETON_HOME_EDGE_01_TAILSCALE_IP": "100.64.0.88",
            "SKELETON_HOME_EDGE_01_TARGET_USER": "runtime-user",
            "SKELETON_HOME_EDGE_01_CONTROLLER_HOST": "runtime-controller",
            "SKELETON_HOME_EDGE_01_CONTROLLER_TAILSCALE_IP": "100.64.0.99",
            "SKELETON_HOME_EDGE_01_PRIMARY_INTERFACE": "runtime0",
            "SKELETON_HOME_EDGE_01_PRIMARY_GATEWAY": "192.0.2.254",
        }
    )

    assert profile.node_id == "runtime-node"
    assert profile.target == "runtime-user@100.64.0.88"
    assert profile.controller_host == "runtime-controller"
    assert profile.primary_network["interface"] == "runtime0"
    assert profile.primary_network["gateway"] == "192.0.2.254"


def test_profile_loads_runtime_values_from_local_path(tmp_path: Path) -> None:
    data = json.loads(Path("config/home_edge/home-edge-01.json").read_text(encoding="utf-8"))
    data["node_id"] = "local-node"
    data["ssh"]["target_user"] = "local-user"
    path = tmp_path / "home-edge.local.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    profile = load_home_edge_profile(environment={LOCAL_PROFILE_ENV: str(path)})

    assert profile.node_id == "local-node"
    assert profile.target_user == "local-user"


def test_profile_rejects_disabled_strict_host_key_checking(tmp_path: Path) -> None:
    source = Path("config/home_edge/home-edge-01.json")
    data = json.loads(source.read_text(encoding="utf-8"))
    data["ssh"]["strict_host_key_checking"] = False
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="strict host key"):
        load_home_edge_profile(path, environment={})
