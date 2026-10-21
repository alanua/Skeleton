from __future__ import annotations

import json

from scripts.family_dns_preflight import main


def test_preflight_cli_prints_nonmutating_public_packet(capsys) -> None:
    code = main(["--request-prefix", "unit-family-dns"])

    assert code == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["schema"] == "skeleton.family_dns.preflight.v1"
    assert packet["mode"] == "read_only_nonmutating"
    assert packet["node_id"] == "home-edge-01"
    assert packet["mutation_allowed"] is False
    assert packet["runtime_switch_allowed"] is False
    assert packet["preflight_requests"][0]["request_id"].startswith("unit-family-dns-")
    assert "stage_b_registered_approved_control" in packet["policy"]["stages"][1]["blocked_until"]
    assert packet["policy"]["stages"][1]["gates"][0]["passed"] is False
    assert packet["policy"]["expectations"]["android_system_dot_certificate_required"] is False
    assert packet["policy"]["expectations"]["preferred_away_path"] == "existing_encrypted_tailscale_tunnel_to_private_dns"
    assert "not_sent" in packet["policy"]["expectations"]["outcome_model"]
