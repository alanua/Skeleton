from __future__ import annotations

from pathlib import Path

import pytest

from core.home_edge.youtube_remote import (
    MAX_PROGRESS_AGE_SECONDS,
    PROGRESS_AWARE_BUTTONS,
    YOUTUBE_BROKER_MAPPING,
    YOUTUBE_BUTTON_ALLOWLIST,
    ProgressSafety,
    RecordingYouTubeBrokerSink,
    YouTubeBrokerCommand,
    YouTubeButton,
    YouTubeButtonEvent,
    YouTubePhase,
    YouTubeProgressContext,
    YouTubeRemoteBroker,
    YouTubeRemoteContractError,
    validate_youtube_remote_contract,
)


ROOT = Path(__file__).resolve().parents[1]


def test_youtube_button_contract_matches_broker_mapping() -> None:
    validate_youtube_remote_contract()

    assert set(YOUTUBE_BROKER_MAPPING) == set(YOUTUBE_BUTTON_ALLOWLIST)
    assert PROGRESS_AWARE_BUTTONS <= YOUTUBE_BUTTON_ALLOWLIST
    assert not any(
        action.startswith(("ssh", "adb", "xdotool", "input keyevent"))
        for action in YOUTUBE_BROKER_MAPPING.values()
    )


def test_dispatch_emits_typed_offline_broker_command_with_fresh_progress_proof() -> None:
    sink = RecordingYouTubeBrokerSink()
    broker = YouTubeRemoteBroker(sink=sink)
    progress = YouTubeProgressContext(
        video_ref="yt_AbCdEf12345",
        position_seconds=100,
        duration_seconds=600,
        observed_at=10,
        playing=True,
    )

    (command,) = broker.dispatch(
        YouTubeButtonEvent(YouTubeButton.SEEK_FORWARD_10, YouTubePhase.TAP, progress=progress),
        now=14,
    )

    assert isinstance(command, YouTubeBrokerCommand)
    assert sink.commands == [command]
    assert command.action == "home.youtube.transport.seek_forward_10"
    assert command.progress_proof.safety is ProgressSafety.FRESH
    assert command.progress_proof.bounded is True
    assert command.progress_proof.observed_age_seconds == 4
    assert command.progress_proof.estimated_position_seconds == 104


def test_stale_progress_context_is_preserved_but_not_treated_as_fresh() -> None:
    sink = RecordingYouTubeBrokerSink()
    broker = YouTubeRemoteBroker(sink=sink)
    progress = YouTubeProgressContext(
        video_ref="yt_AbCdEf12345",
        position_seconds=100,
        duration_seconds=600,
        observed_at=10,
        playing=True,
    )

    (command,) = broker.dispatch(
        YouTubeButtonEvent(YouTubeButton.PLAY_PAUSE, YouTubePhase.TAP, progress=progress),
        now=10 + MAX_PROGRESS_AGE_SECONDS + 1,
    )

    assert command.progress_proof.safety is ProgressSafety.STALE
    assert "must not drive optimistic seek state" in command.progress_proof.reason
    assert command.progress_proof.estimated_position_seconds == 100


def test_non_progress_button_has_absent_progress_proof() -> None:
    sink = RecordingYouTubeBrokerSink()
    broker = YouTubeRemoteBroker(sink=sink)

    (command,) = broker.dispatch(YouTubeButtonEvent(YouTubeButton.DPAD_UP, YouTubePhase.DOWN), now=10)

    assert command.progress_proof.safety is ProgressSafety.ABSENT
    assert command.progress_proof.bounded is True


@pytest.mark.parametrize(
    "progress",
    [
        YouTubeProgressContext("https://www.youtube.com/watch?v=AbCdEf12345", 1, 10, 0, False),
        YouTubeProgressContext("yt_AbCdEf12345?token=secret", 1, 10, 0, False),
        YouTubeProgressContext("yt_AbCdEf12345", -1, 10, 0, False),
        YouTubeProgressContext("yt_AbCdEf12345", 11, 10, 0, False),
        YouTubeProgressContext("yt_AbCdEf12345", 1, 10, 11, False),
    ],
)
def test_progress_context_rejects_urls_queries_invalid_ranges_and_future_observations(
    progress: YouTubeProgressContext,
) -> None:
    sink = RecordingYouTubeBrokerSink()
    broker = YouTubeRemoteBroker(sink=sink)

    with pytest.raises(YouTubeRemoteContractError):
        broker.dispatch(
            YouTubeButtonEvent(YouTubeButton.PLAY_PAUSE, YouTubePhase.TAP, progress=progress),
            now=10,
        )


def test_source_and_docs_do_not_contain_live_device_control_paths() -> None:
    sources = [
        ROOT / "core/home_edge/youtube_remote.py",
        ROOT / "docs/HOME_EDGE_YOUTUBE_REMOTE.md",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in sources)

    assert not any(value in combined for value in ("subprocess", "paramiko", "socket.", "requests.", "adb shell"))
