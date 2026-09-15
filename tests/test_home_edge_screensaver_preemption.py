from __future__ import annotations

import json
import subprocess

import pytest

from scripts import home_edge_screensaver_preemption as preemption


def _owner_payload(state: str) -> str:
    return json.dumps(
        {
            "schema_version": "skeleton.home_edge.media_display_ownership.v1",
            "state": state,
            "reason": "public_reason",
            "observations": [],
        }
    )


def test_clear_owner_status_allows_saver_and_uses_canonical_command() -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 1, _owner_payload("CLEAR"), "")

    result = preemption.decide_screensaver_preemption(timeout_seconds=1.25, runner=fake_run)

    assert result.show_saver is True
    assert result.exit_code == 0
    assert result.reason == "owner_status_clear"
    assert calls == [
        (
            ["home-edge-media-display-owner", "status"],
            {"text": True, "capture_output": True, "timeout": 1.25, "check": False},
        )
    ]


@pytest.mark.parametrize(
    ("returncode", "state", "reason"),
    [
        (0, "OWNER", "owner_status_owner"),
        (2, "UNKNOWN", "owner_status_unknown"),
    ],
)
def test_owner_and_unknown_status_suppress_saver(returncode: int, state: str, reason: str) -> None:
    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, returncode, _owner_payload(state), "")

    result = preemption.decide_screensaver_preemption(runner=fake_run)

    assert result.show_saver is False
    assert result.exit_code == 1
    assert result.reason == reason
    assert result.owner_exit_code == returncode


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [
        (1, ""),
        (1, "{not-json"),
        (1, _owner_payload("OWNER")),
        (0, _owner_payload("CLEAR")),
        (127, _owner_payload("UNKNOWN")),
    ],
)
def test_malformed_status_fails_closed(returncode: int, stdout: str) -> None:
    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, returncode, stdout, "private stderr")

    result = preemption.decide_screensaver_preemption(runner=fake_run)

    assert result.show_saver is False
    assert result.exit_code == 1
    assert result.reason == "owner_status_malformed"
    assert "private stderr" not in preemption.public_json(result)


def test_timeout_fails_closed_without_showing_saver() -> None:
    def fake_run(_argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(preemption.OWNER_STATUS_COMMAND, timeout=2.0)

    result = preemption.decide_screensaver_preemption(runner=fake_run)

    assert result.show_saver is False
    assert result.exit_code == 1
    assert result.reason == "owner_status_timeout"


def test_public_payload_is_stable_and_public_safe() -> None:
    result = preemption.ScreensaverPreemptionResult(False, "owner_status_owner", 0)
    assert result.public_payload() == {
        "schema_version": "skeleton.home_edge.screensaver_preemption.v1",
        "show_saver": False,
        "reason": "owner_status_owner",
        "owner_exit_code": 0,
    }
