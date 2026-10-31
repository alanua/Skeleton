from __future__ import annotations

from dataclasses import replace

import pytest

from core.home_edge.media_playback_stall import PlaybackObservation, verify_playback


def samples(**changes: object) -> list[PlaybackObservation]:
    base = PlaybackObservation(
        endpoint="synthetic-screen", media_id="youtube:synthetic", session_id="session-1",
        mode="browser", observed_at=100, position=20, video_frames=600,
        ready_state=4, buffered_ahead=12, window_state="mapped", has_video=True,
    )
    return [replace(base, observed_at=100 + i, **changes) for i in range(3)]


def test_frozen_youtube_has_data_but_no_video_progress() -> None:
    result = verify_playback(samples(), now=102)
    assert result.status == "stalled"
    assert result.action == "try_pause_play"


@pytest.mark.parametrize("mode", ["browser", "mpv"])
def test_advancing_video_is_verified_without_claiming_visibility(mode: str) -> None:
    history = [replace(s, position=20 + i, video_frames=600 + 30 * i)
               for i, s in enumerate(samples(mode=mode))]
    result = verify_playback(history, now=102)
    assert result.status == "playing"
    assert "visibility unproven" in result.reason


@pytest.mark.parametrize("changes,status", [
    ({"paused": True}, "paused"),
    ({"buffering": True, "ready_state": 2, "buffered_ahead": 0}, "buffering"),
    ({"seeking": True}, "seeking"),
    ({"connected": False}, "stale"),
    ({"window_state": "unmapped"}, "unmapped"),
    ({"window_state": "Withdrawn"}, "unmapped"),
    ({"window_state": "ghost"}, "unmapped"),
    ({"window_state": "unknown"}, "unknown"),
    ({"video_frames": None}, "unknown"),
    ({"mode": "mpv", "has_video": False, "video_frames": None}, "unknown"),
])
def test_distinct_nonplaying_evidence(changes: dict, status: str) -> None:
    assert verify_playback(samples(**changes), now=102).status == status


@pytest.mark.parametrize("field,value", [
    ("endpoint", "other"), ("media_id", "other"),
    ("session_id", "other"), ("mode", "mpv"),
])
def test_no_comparison_across_identity_changes(field: str, value: str) -> None:
    history = samples()
    history[-1] = replace(history[-1], **{field: value})
    assert verify_playback(history, now=102).status == "swapped_media"


@pytest.mark.parametrize("times,now", [
    ([100, 101, 102], 120), ([100, 100, 102], 102),
    ([101, 100, 102], 102), ([90, 101, 102], 102),
    ([100, 101, 103], 102),
])
def test_stale_future_unordered_and_gapped_samples(times: list[int], now: int) -> None:
    history = [replace(s, observed_at=t) for s, t in zip(samples(), times)]
    assert verify_playback(history, now=now).status == "stale"


@pytest.mark.parametrize("position,frames", [(1, 601), (90, 601), (20, 1)])
def test_discontinuities_are_seeks(position: float, frames: int) -> None:
    history = samples()
    history[-1] = replace(history[-1], position=position, video_frames=frames)
    assert verify_playback(history, now=102).status == "seeking"


def test_single_interval_cannot_prove_stall_or_recovery() -> None:
    assert verify_playback(samples()[:2], now=102).status == "pending"
    history = samples()
    history[-1] = replace(history[-1], position=21, video_frames=630)
    assert verify_playback(history, now=102).status == "pending"
    history.append(replace(history[-1], observed_at=103, position=22, video_frames=660))
    assert verify_playback(history, now=103).status == "playing"


def test_successful_rpc_does_not_recover_and_retry_budget_is_finite() -> None:
    history = samples(command_acknowledged=True)
    actions = [verify_playback(history, now=102, recovery_attempts=n).action for n in range(10)]
    assert actions[:2] == ["try_pause_play", "try_pause_play"]
    assert actions[2:] == ["manual_check"] * 8
    assert all(verify_playback(history, now=102, recovery_attempts=n).status == "stalled"
               for n in range(10))


@pytest.mark.parametrize("position,frames", [(21, 600), (20, 630)])
def test_time_or_decoder_progress_alone_is_not_verified(position: float, frames: int) -> None:
    history = [replace(s, position=20 + i * (position - 20), video_frames=600 + i * (frames - 600))
               for i, s in enumerate(samples())]
    assert verify_playback(history, now=102).status == "pending"


@pytest.mark.parametrize("changes", [
    {"position": float("nan")}, {"position": -1}, {"position": float("inf")},
    {"observed_at": -1}, {"video_frames": -1}, {"video_frames": 1.5},
    {"ready_state": 5}, {"buffered_ahead": -1}, {"media_id": ""},
])
def test_invalid_telemetry_is_unknown(changes: dict) -> None:
    history = samples()
    history[-1] = replace(history[-1], **changes)
    assert verify_playback(history, now=102).status == "unknown"


@pytest.mark.parametrize("count", [0, 1, 65])
def test_observation_count_is_bounded(count: int) -> None:
    assert verify_playback((samples() * 22)[:count], now=102).status == "unknown"


@pytest.mark.parametrize("policy", [
    {"now": float("nan")}, {"max_gap": 0}, {"max_age": -1},
    {"confirmations": 1}, {"confirmations": 64}, {"recovery_attempts": -1},
])
def test_invalid_policy_is_rejected(policy: dict) -> None:
    with pytest.raises(ValueError):
        verify_playback(samples(), **({"now": 102} | policy))


def test_unknown_buffer_evidence_does_not_prove_stall() -> None:
    assert verify_playback(samples(buffered_ahead=None), now=102).status == "pending"
    assert verify_playback(samples(ready_state=None), now=102).status == "pending"


def test_pause_resets_confirmation_and_inputs_are_immutable() -> None:
    history = samples()
    history[1] = replace(history[1], paused=True)
    original = tuple(history)
    assert verify_playback(history, now=102).status == "pending"
    assert tuple(history) == original
