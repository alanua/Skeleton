from __future__ import annotations

import json

from core.home_edge import display_power_off as display
from scripts import home_edge_display_power_off_signer as signer


def test_fixed_signer_accepts_no_argv_and_emits_one_display_off_operation(monkeypatch, capsys) -> None:
    monkeypatch.setattr(display, "read_fixed_controller_hmac_secret", lambda: "synthetic-secret")

    assert signer.main([]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["node_id"] == "home-edge-01"
    assert payload["execution_lane"] == "routine_mutation"
    assert payload["operator_approval_ref"] == display.OPERATOR_APPROVAL
    assert payload["run_as"] == "desktop-user"
    assert payload["mode"] == "script"
    assert payload["argv"] == []
    assert "home_edge_01_display_power_off_v1" in payload["request_id"]
    assert payload["signature"].startswith("sha256=")


def test_fixed_signer_rejects_argv_authority_broadening(capsys) -> None:
    assert signer.main(["--task-id", "other"]) == 2
    assert "accepts no operation arguments" in capsys.readouterr().err
