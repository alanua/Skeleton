from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.home_edge.profile import load_home_edge_profile


def test_home_edge_profile_registers_universal_fixed_node() -> None:
    profile = load_home_edge_profile()

    assert profile.node_id == "home-edge-01"
    assert profile.hostname == "home-edge-01"
    assert profile.tailscale_ip == "100.127.35.74"
    assert profile.target_user == "valertos08"
    assert profile.transport == "openssh_over_tailscale_ip"
    assert profile.identity_env == "SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE"
    assert profile.known_hosts_env == "SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE"
    assert profile.task_model == "typed_allowlisted_actions"
    assert "browser_and_desktop_diagnostics" in profile.capabilities
    assert "home_automation" in profile.capabilities
    assert profile.primary_network["interface"] == "enp1s0"


def test_profile_rejects_changed_target_identity(tmp_path: Path) -> None:
    source = Path("config/home_edge/home-edge-01.json")
    data = json.loads(source.read_text(encoding="utf-8"))
    data["tailscale_ip"] = "203.0.113.8"
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="fixed contract mismatch"):
        load_home_edge_profile(path)
