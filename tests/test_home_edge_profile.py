from __future__ import annotations

from core.home_edge.profile import load_home_edge_profile


def test_home_edge_profile_registers_expected_node() -> None:
    profile = load_home_edge_profile()

    assert profile.node_id == "home-edge-01"
    assert profile.hostname == "home-edge-01"
    assert profile.tailscale_ip == "100.127.35.74"
    assert profile.target_user == "valertos08"
    assert "usb_modem_diagnostics" in profile.capabilities
    assert profile.primary_network["interface"] == "enp1s0"
