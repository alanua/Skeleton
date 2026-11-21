"""Pure, bounded kiosk evidence contract; no runtime probes or action execution.

All observations must describe the same seat/TTY and monotonic clock. Compositor
visibility is independent of X11 focus. PHYSICALLY_VERIFIED is reserved for an
external physical observation protocol and is never inferred by this evaluator.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Literal


class Status(str, Enum):
    UNKNOWN = "UNKNOWN"
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    APPLIED = "APPLIED"
    PHYSICALLY_VERIFIED = "PHYSICALLY_VERIFIED"


Reason = Literal[
    "invalid_or_stale_sample", "inconsistent_tty", "not_kiosk_youtube",
    "chrome_absent", "missing_evidence", "visibility_conflict", "mpv_overlay",
    "mpv_mapping_unknown", "chrome_occluded", "visibility_unobserved",
    "different_media", "user_paused", "pause_unattributed", "buffering",
    "progress_unobserved", "frozen_progress", "playback_advancing",
]
Foreground = Literal["visible", "obscured", "at_risk", "unknown", "not_applicable"]
Playback = Literal["unknown", "paused", "buffering", "frozen", "advancing"]
Confidence = Literal["low", "medium", "high"]
NextStep = Literal[
    "unmap_mpv_surface_via_registered_path", "review_kiosk_visibility",
    "stop_mpv_via_guarded_existing_operation", "none",
]


@dataclass(frozen=True)
class Snapshot:
    sampled_at: float
    mode: str | None = None
    tty: str | None = None
    chrome_running: bool | None = None
    youtube_backend: str | None = None
    chrome_xwayland_foreground: bool | None = None
    mpv_running: bool | None = None
    mpv_paused: bool | None = None
    mpv_wayland_mapped: bool | None = None
    mpv_fullscreen: bool | None = None
    mpv_always_on_top: bool | None = None
    chrome_visible_unobscured: bool | None = None
    paused: bool | None = None
    user_paused: bool | None = None
    buffering: bool | None = None
    playback_position: float | None = None
    frame_counter: int | None = None
    video_identity: str | None = None
    command_sent: bool | None = None
    command_accepted: bool | None = None
    command_applied: bool | None = None


@dataclass(frozen=True)
class Outcome:
    status: Status
    foreground: Foreground
    playback: Playback
    reason: Reason
    confidence: Confidence
    next_step: NextStep


def evaluate(
    current: Snapshot, previous: Snapshot | None = None, *, now: float,
    max_age: float = 5.0, max_interval: float = 5.0,
) -> Outcome:
    """Recommend only; callers own verification and any registered operation.

    Bounds are seconds. Both samples must be fresh; repeated/reversed timestamps
    are rejected. Missing identity prevents comparing progress across samples.
    Status describes command evidence only, never visibility or playback health.
    """
    status = Status.UNKNOWN
    if current.command_sent is True:
        status = Status.SENT
        if current.command_accepted is True:
            status = Status.ACCEPTED
            if current.command_applied is True:
                status = Status.APPLIED

    def result(reason: Reason, foreground: Foreground = "unknown",
               playback: Playback = "unknown", confidence: Confidence = "low", next_step: NextStep = "review_kiosk_visibility") -> Outcome:
        return Outcome(status, foreground, playback, reason, confidence, next_step)

    samples = (current,) if previous is None else (previous, current)
    if (not all(isfinite(v) for v in (now, max_age, max_interval))
            or max_age <= 0 or max_interval <= 0
            or any(not isfinite(s.sampled_at) or not 0 <= now - s.sampled_at <= max_age
                   for s in samples)
            or (previous is not None
                and not 0 < current.sampled_at - previous.sampled_at <= max_interval)):
        return result("invalid_or_stale_sample")
    if previous is not None and previous.tty != current.tty:
        return result("inconsistent_tty")
    if current.mode is None or current.youtube_backend is None or not current.tty:
        return result("missing_evidence")
    if current.mode != "kiosk" or current.youtube_backend not in ("youtube-web", "chrome"):
        return result("not_kiosk_youtube", "not_applicable", next_step="none")
    if current.chrome_running is False:
        return result("chrome_absent", confidence="high")
    if current.chrome_running is None:
        return result("missing_evidence")

    overlay = (current.mpv_running is True and current.mpv_fullscreen is True
               and current.mpv_always_on_top is True)
    if current.chrome_visible_unobscured is True and current.mpv_wayland_mapped is True:
        return result("visibility_conflict", "at_risk", confidence="high")
    if overlay and current.mpv_wayland_mapped is True:
        return result("mpv_overlay", "obscured", confidence="high",
                      next_step="unmap_mpv_surface_via_registered_path")
    if overlay and current.mpv_wayland_mapped is None:
        return result("mpv_mapping_unknown")
    if current.chrome_visible_unobscured is False:
        return result("chrome_occluded", "obscured", confidence="high")
    if current.chrome_visible_unobscured is not True:
        return result("visibility_unobserved")

    def visible(reason: Reason, playback: Playback = "unknown",
                confidence: Confidence = "medium") -> Outcome:
        return result(reason, "visible", playback, confidence, "none")

    if current.user_paused is True and current.paused is True:
        return visible("user_paused", "paused", "high")
    if current.buffering is True:
        return visible("buffering", "buffering", "high")
    if current.paused is True:
        return visible("pause_unattributed", "paused")
    if previous is None:
        return visible("progress_unobserved")
    if (not current.video_identity or not previous.video_identity):
        return visible("progress_unobserved")
    if current.video_identity != previous.video_identity:
        return visible("different_media")
    if previous.mode != current.mode or previous.youtube_backend != current.youtube_backend:
        return visible("progress_unobserved")
    if current.paused is not False or current.buffering is not False:
        return visible("progress_unobserved")
    if previous.paused is not False or previous.buffering is not False:
        return visible("progress_unobserved")
    positions = (previous.playback_position, current.playback_position)
    frames = (previous.frame_counter, current.frame_counter)
    if any(p is None or not isfinite(p) or p < 0 for p in positions):
        return visible("progress_unobserved")
    if any(f is not None and (f < 0 or not isinstance(f, int)) for f in frames):
        return visible("progress_unobserved")
    if positions[1] < positions[0] or (None not in frames and frames[1] < frames[0]):
        return visible("progress_unobserved")
    if None not in frames and frames[1] == frames[0]:
        return visible("frozen_progress", "frozen", "high")
    if positions[1] > positions[0] or (None not in frames and frames[1] > frames[0]):
        return visible("playback_advancing", "advancing")
    return visible("frozen_progress", "frozen")
