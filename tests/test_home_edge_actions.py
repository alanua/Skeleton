from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from core.home_edge.actions import (
    ALLOWED_ACTIONS,
    HomeEdgeActionError,
    build_remote_action_program,
    execute_home_edge_action,
    validate_action_request,
)
from core.home_edge.diagnostics import ProbeResult


NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


class FakeActionTransport:
    def __init__(self, response: dict | None = None) -> None:
        self.response = response or {"status": "observed", "value": {"ok": True}}
        self.calls: list[tuple[str, int]] = []

    def run_action(self, payload: str, *, timeout_seconds: int) -> ProbeResult:
        self.calls.append((payload, timeout_seconds))
        return ProbeResult(
            state="observed",
            adapter="fake",
            stdout=json.dumps(self.response),
            exit_code=0,
        )


def request(action_id: str, parameters: dict | None = None) -> dict:
    return {
        "node_id": "home-edge-01",
        "action_id": action_id,
        "request_id": "req-00000001",
        "timestamp": NOW.isoformat(),
        "nonce": "nonce000000000001",
        "idempotency_key": "idem-00000001",
        "parameters": parameters or {},
    }


def test_exact_direct_action_allowlist() -> None:
    assert ALLOWED_ACTIONS == {
        "media.get_volume",
        "media.set_volume",
        "media.mute",
        "media.unmute",
        "media.playback_status",
        "home_edge.health",
        "home_edge.diagnostic",
        "tv.get_mode",
        "tv.set_mode",
    }


@pytest.mark.parametrize("level", [0, 1, 50, 99, 100])
def test_volume_boundaries_are_valid(level: int) -> None:
    validated = validate_action_request(
        request("media.set_volume", {"level": level, "target": "host"})
    )

    assert validated["parameters"]["level"] == level


@pytest.mark.parametrize("level", [-1, 101, 1.5, "100", True])
def test_invalid_percentages_rejected_before_transport(level: object) -> None:
    with pytest.raises(HomeEdgeActionError):
        validate_action_request(request("media.set_volume", {"level": level, "target": "host"}))


def test_invalid_targets_and_extra_fields_rejected_before_transport() -> None:
    with pytest.raises(HomeEdgeActionError, match="unsupported target"):
        validate_action_request(request("media.set_volume", {"level": 50, "target": "speaker"}))

    bad = request("media.set_volume", {"level": 50, "target": "host", "shell": "x"})
    with pytest.raises(HomeEdgeActionError, match="unsupported parameters"):
        validate_action_request(bad)

    bad_request = request("media.get_volume")
    bad_request["path"] = "/tmp/private"
    with pytest.raises(HomeEdgeActionError, match="unsupported request fields"):
        validate_action_request(bad_request)


def test_host_volume_uses_fixed_argv_and_verifies_readback() -> None:
    transport = FakeActionTransport({"status": "observed", "value": {"host": {"level": 100}}})
    receipt = execute_home_edge_action(
        request("media.set_volume", {"level": 100, "target": "host"}),
        transport=transport,
        now=NOW,
    )
    payload = transport.calls[0][0]

    assert receipt["verified"] is True
    assert '"media.set_volume"' in payload
    assert '["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{level}%"]' in payload
    assert '["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"]' in payload
    assert "shell=True" not in payload


def test_android_volume_uses_fixed_argv_and_verifies_readback() -> None:
    transport = FakeActionTransport({"status": "observed", "value": {"android_tv": {"level": 100}}})
    execute_home_edge_action(
        request("media.set_volume", {"level": 100, "target": "android_tv"}),
        transport=transport,
        now=NOW,
    )
    payload = transport.calls[0][0]

    assert '["waydroid", "shell"] + args' in payload
    assert '["cmd", "media_session", "volume", "--set", str(android_level)]' in payload
    assert '["settings", "put", "system", "volume_music", str(android_level)]' in payload


def test_target_both_reports_deterministic_partial_failure() -> None:
    transport = FakeActionTransport(
        {
            "status": "partial_failure",
            "value": {"host": {"level": 50}, "android_tv": None},
            "errors": [{"target": "android_tv", "code": "unsupported"}],
        }
    )

    receipt = execute_home_edge_action(
        request("media.set_volume", {"level": 50, "target": "both"}),
        transport=transport,
        now=NOW,
    )

    assert receipt["status"] == "partial_failure"
    assert receipt["verified"] is False
    assert receipt["errors"] == [{"target": "android_tv", "code": "unsupported"}]


def test_mute_unmute_and_mode_actions_use_fixed_adapters_only() -> None:
    for action_id, parameters in (
        ("media.mute", {"target": "both"}),
        ("media.unmute", {"target": "both"}),
        ("tv.set_mode", {"mode": "waydroid"}),
    ):
        payload = build_remote_action_program(action_id, parameters)
        assert "subprocess.run(" in payload
        assert "shell=True" not in payload
        assert "os.system" not in payload
        assert "cmd = ENVELOPE" not in payload


def test_tv_off_requires_separate_confirmation() -> None:
    with pytest.raises(HomeEdgeActionError, match="explicit confirmation"):
        validate_action_request(request("tv.set_mode", {"mode": "off"}))

    validated = validate_action_request(
        request("tv.set_mode", {"mode": "off", "confirm_off": True})
    )

    assert validated["parameters"]["mode"] == "off"


def test_wrong_node_and_action_rejected_before_transport() -> None:
    wrong_node = request("media.get_volume")
    wrong_node["node_id"] = "other"
    with pytest.raises(HomeEdgeActionError, match="wrong node"):
        validate_action_request(wrong_node)

    with pytest.raises(HomeEdgeActionError, match="not allowlisted"):
        validate_action_request(request("media.raw_shell"))


def test_receipts_do_not_expose_raw_output_or_private_identifiers() -> None:
    transport = FakeActionTransport(
        {
            "status": "observed",
            "value": {
                "hostname": "runtime-host",
                "address": "100.64.10.74",
                "level": 100,
            },
        }
    )

    receipt = execute_home_edge_action(
        request("media.get_volume"),
        transport=transport,
        now=NOW,
    )
    rendered = json.dumps(receipt, sort_keys=True)

    assert "runtime-host" not in rendered
    assert "100.64.10.74" not in rendered
    assert "stdout" not in rendered
    assert receipt["result"]["hostname"] == "[redacted]"
