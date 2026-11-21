"""Pure verification of synthetic video telemetry; never contacts a player.

All times are seconds on one monotonic clock. Frame counts must be cumulative
video decoded/presented counts for the identified session, never audio counters.
Verified progress does not establish physical screen visibility. Callers own the
finite recovery-attempt count; command acknowledgements are not evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal, Sequence

Status = Literal[
    "playing", "paused", "seeking", "buffering", "stalled", "pending",
    "unknown", "swapped_media", "stale", "unmapped",
]
Action = Literal["none", "observe", "check_window", "try_pause_play", "manual_check"]

MAX_OBSERVATIONS = 64
MAX_SECONDS = 1_000_000_000_000.0
MAX_POSITION = 31_536_000.0
MAX_FRAMES = 2**53 - 1
MAX_RECOVERY_ATTEMPTS = 2


@dataclass(frozen=True)
class PlaybackObservation:
    endpoint: str
    media_id: str
    session_id: str
    mode: Literal["browser", "mpv"]
    observed_at: float
    position: float
    video_frames: int | None
    paused: bool = False
    seeking: bool = False
    buffering: bool = False
    ready_state: int | None = None
    buffered_ahead: float | None = None
    connected: bool = True
    window_state: Literal["mapped", "unmapped", "Withdrawn", "ghost", "unknown"] = "unknown"
    has_video: bool | None = None
    command_acknowledged: bool = False


@dataclass(frozen=True)
class PlaybackDecision:
    status: Status
    reason: str
    action: Action


def _bounded(value: float, upper: float) -> bool:
    return not isinstance(value, bool) and isfinite(value) and 0 <= value <= upper


def verify_playback(
    observations: Sequence[PlaybackObservation],
    *,
    now: float,
    max_age: float = 10.0,
    max_gap: float = 5.0,
    confirmations: int = 2,
    recovery_attempts: int = 0,
) -> PlaybackDecision:
    """Require consecutive confirming intervals, defaulting to three samples.

    Two samples are accepted but one interval cannot establish playing/stalled.
    A discontinuity resets confirmation. Each interval needs both video-position
    and frame evidence. At most two recovery suggestions may be acted on; pass
    the number already attempted, even if RPCs succeeded. No retries occur here.
    Malformed policy raises ValueError; unusable telemetry returns unknown.
    """
    if (
        not _bounded(now, MAX_SECONDS)
        or not _bounded(max_age, 3600) or max_age == 0
        or not _bounded(max_gap, 3600) or max_gap == 0
        or type(confirmations) is not int or not 2 <= confirmations < MAX_OBSERVATIONS
        or type(recovery_attempts) is not int or recovery_attempts < 0
    ):
        raise ValueError("invalid verification policy")
    if not 2 <= len(observations) <= MAX_OBSERVATIONS:
        return PlaybackDecision("unknown", "need 2..64 observations", "observe")
    first = observations[0]
    for sample in observations:
        if (
            not all((sample.endpoint, sample.media_id, sample.session_id))
            or sample.mode not in ("browser", "mpv")
            or not _bounded(sample.observed_at, MAX_SECONDS)
            or not _bounded(sample.position, MAX_POSITION)
            or (sample.video_frames is not None and (
                type(sample.video_frames) is not int or not 0 <= sample.video_frames <= MAX_FRAMES
            ))
            or (sample.ready_state is not None and (
                type(sample.ready_state) is not int or not 0 <= sample.ready_state <= 4
            ))
            or (sample.buffered_ahead is not None and not _bounded(sample.buffered_ahead, MAX_POSITION))
        ):
            return PlaybackDecision("unknown", "invalid telemetry", "observe")
        if (sample.endpoint, sample.media_id, sample.session_id, sample.mode) != (
            first.endpoint, first.media_id, first.session_id, first.mode
        ):
            return PlaybackDecision("swapped_media", "identity or mode changed", "observe")
        if not sample.connected or not 0 <= now - sample.observed_at <= max_age:
            return PlaybackDecision("stale", "disconnected, stale or future observation", "observe")
    for previous, current in zip(observations, observations[1:]):
        if not 0 < current.observed_at - previous.observed_at <= max_gap:
            return PlaybackDecision("stale", "unordered or separated observations", "observe")
    latest = observations[-1]
    if latest.window_state in ("unmapped", "Withdrawn", "ghost"):
        return PlaybackDecision("unmapped", "window is not mapped", "check_window")
    if latest.window_state != "mapped":
        return PlaybackDecision("unknown", "window mapping unconfirmed", "observe")
    if latest.seeking:
        return PlaybackDecision("seeking", "seek in progress", "observe")
    if latest.paused:
        return PlaybackDecision("paused", "user pause reported", "none")
    if any(s.has_video is not True or s.video_frames is None for s in observations):
        return PlaybackDecision("unknown", "video frame confirmation unavailable (possibly audio-only)", "observe")

    candidate: Status = "pending"
    streak = 0
    for previous, current in zip(observations, observations[1:]):
        interval: Status = "pending"
        assert previous.video_frames is not None and current.video_frames is not None
        delta = current.position - previous.position
        frames = current.video_frames - previous.video_frames
        elapsed = current.observed_at - previous.observed_at
        if current.window_state != "mapped" or previous.window_state != "mapped":
            interval = "pending"
        elif current.seeking or previous.seeking or delta < 0 or delta > elapsed * 4 + 0.5 or frames < 0:
            interval = "seeking"
        elif current.paused or previous.paused:
            interval = "pending"
        elif delta > 0.01 and frames > 0:
            interval = "playing"
        elif delta <= 0.01 and frames == 0:
            if current.buffering and current.ready_state is not None and current.ready_state < 3 and current.buffered_ahead == 0:
                interval = "buffering"
            elif current.ready_state == 4 and current.buffered_ahead is not None and current.buffered_ahead > 0:
                interval = "stalled"
        streak = streak + 1 if interval == candidate else 1
        candidate = interval
    if candidate == "seeking":
        return PlaybackDecision("seeking", "position or frame discontinuity", "observe")
    if candidate == "pending" or streak < confirmations:
        return PlaybackDecision("pending", "insufficient consecutive progress or stall evidence", "observe")
    if candidate == "playing":
        return PlaybackDecision("playing", "position and video frames advance; visibility unproven", "none")
    if candidate == "buffering":
        return PlaybackDecision("buffering", "empty buffer and insufficient ready state", "observe")
    action: Action = "try_pause_play" if recovery_attempts < MAX_RECOVERY_ATTEMPTS else "manual_check"
    return PlaybackDecision("stalled", "ready data but frozen position and video frames", action)
