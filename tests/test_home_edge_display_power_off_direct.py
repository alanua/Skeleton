from __future__ import annotations

import json

import pytest

from core.home_edge.executor import HomeEdgeExecRequest
from core.home_edge.executor import sign_request
from scripts import home_edge_display_power_off_signer


def test_fixed_signer_rejects_non_display_off_argv(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    request = {
        "request_id": "bad",
        "node_id": "home-edge-01",
        "execution_lane": "routine_mutation",
        "argv": ["xset", "q"],
        "timeout_seconds": 10,
        "operator_approval_ref": (
            "EXPLICIT_HOME_EDGE_01_DISPLAY_POWER_OFF_CONTROLLER_20260809"
        ),
        "idempotency_key": "display-off-literal-risk-field-20260809-v1",
        "run_as": "desktop-user",
        "timestamp": "2026-08-09T00:00:00+00:00",
        "nonce": "nonce",
        "public": True,
    }
    monkeypatch.setattr("sys.stdin.read", lambda: json.dumps(request))

    with pytest.raises(ValueError, match="request argv mismatch"):
        home_edge_display_power_off_signer.main()

    assert capsys.readouterr().out == ""


def test_fixed_signer_uses_secret_file_not_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_path = tmp_path / "controller.hmac"
    secret_path.write_text("file-secret\n", encoding="utf-8")
    request = {
        "request_id": "ok",
        "node_id": "home-edge-01",
        "execution_lane": "routine_mutation",
        "argv": ["xset", "dpms", "force", "off"],
        "timeout_seconds": 10,
        "operator_approval_ref": (
            "EXPLICIT_HOME_EDGE_01_DISPLAY_POWER_OFF_CONTROLLER_20260809"
        ),
        "idempotency_key": "display-off-literal-risk-field-20260809-v1",
        "run_as": "desktop-user",
        "timestamp": "2026-08-09T00:00:00+00:00",
        "nonce": "nonce",
        "public": True,
    }
    monkeypatch.setattr(home_edge_display_power_off_signer, "SECRET_PATH", secret_path)
    monkeypatch.setattr("sys.stdin.read", lambda: json.dumps(request))
    monkeypatch.setenv("SKELETON_HOME_EDGE_EXEC_HMAC_SECRET", "wrong-env-secret")

    assert home_edge_display_power_off_signer.main() == 0
    signed = json.loads(capsys.readouterr().out)
    assert signed["signature"] == sign_request(
        HomeEdgeExecRequest.from_mapping(signed),
        "file-secret",
    )
