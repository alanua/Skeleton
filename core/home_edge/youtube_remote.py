from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic
from typing import Mapping, Protocol


class YouTubeRemoteContractError(ValueError):
    """Raised when a YouTube remote request escapes the closed contract."""


class YouTubeButton(StrEnum):
    BACK = "back"
    DPAD_UP = "dpad_up"
    DPAD_DOWN = "dpad_down"
    DPAD_LEFT = "dpad_left"
    DPAD_RIGHT = "dpad_right"
    OK = "ok"
    PLAY_PAUSE = "play_pause"
    SEEK_BACK_10 = "seek_back_10"
    SEEK_FORWARD_10 = "seek_forward_10"
    PREVIOUS = "previous"
    NEXT = "next"
    CAPTIONS = "captions"
    FULLSCREEN = "fullscreen"
    MUTE = "mute"


class YouTubePhase(StrEnum):
    DOWN = "down"
    UP = "up"
    TAP = "tap"


class ProgressSafety(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    ABSENT = "absent"


YOUTUBE_BUTTON_ALLOWLIST = frozenset(YouTubeButton)
YOUTUBE_PHASE_ALLOWLIST = frozenset(YouTubePhase)
MAX_PROGRESS_AGE_SECONDS = 30.0

YOUTUBE_BROKER_MAPPING: Mapping[YouTubeButton, str] = {
    YouTubeButton.BACK: "home.youtube.nav.back",
    YouTubeButton.DPAD_UP: "home.youtube.nav.up",
    YouTubeButton.DPAD_DOWN: "home.youtube.nav.down",
    YouTubeButton.DPAD_LEFT: "home.youtube.nav.left",
    YouTubeButton.DPAD_RIGHT: "home.youtube.nav.right",
    YouTubeButton.OK: "home.youtube.nav.ok",
    YouTubeButton.PLAY_PAUSE: "home.youtube.transport.play_pause",
    YouTubeButton.SEEK_BACK_10: "home.youtube.transport.seek_back_10",
    YouTubeButton.SEEK_FORWARD_10: "home.youtube.transport.seek_forward_10",
    YouTubeButton.PREVIOUS: "home.youtube.transport.previous",
    YouTubeButton.NEXT: "home.youtube.transport.next",
    YouTubeButton.CAPTIONS: "home.youtube.transport.captions",
    YouTubeButton.FULLSCREEN: "home.youtube.transport.fullscreen",
    YouTubeButton.MUTE: "home.youtube.audio.mute",
}

PROGRESS_AWARE_BUTTONS = frozenset(
    {
        YouTubeButton.PLAY_PAUSE,
        YouTubeButton.SEEK_BACK_10,
        YouTubeButton.SEEK_FORWARD_10,
        YouTubeButton.PREVIOUS,
        YouTubeButton.NEXT,
    }
)


@dataclass(frozen=True)
class YouTubeProgressContext:
    video_ref: str
    position_seconds: float
    duration_seconds: float
    observed_at: float
    playing: bool

    def validate(self, *, now: float, max_age_seconds: float = MAX_PROGRESS_AGE_SECONDS) -> ProgressSafety:
        if not self.video_ref.strip():
            raise YouTubeRemoteContractError("video_ref must be non-empty")
        if any(value in self.video_ref for value in ("://", "/", "\\", "?", "&", "=", " ")):
            raise YouTubeRemoteContractError("video_ref must be a bounded opaque reference, not a URL or query")
        if self.duration_seconds <= 0:
            raise YouTubeRemoteContractError("duration_seconds must be positive")
        if not 0 <= self.position_seconds <= self.duration_seconds:
            raise YouTubeRemoteContractError("position_seconds must be inside the duration")
        if self.observed_at > now:
            raise YouTubeRemoteContractError("observed_at cannot be in the future")
        age = now - self.observed_at
        return ProgressSafety.FRESH if age <= max_age_seconds else ProgressSafety.STALE

    def estimated_position(self, *, now: float, max_age_seconds: float = MAX_PROGRESS_AGE_SECONDS) -> float:
        safety = self.validate(now=now, max_age_seconds=max_age_seconds)
        if safety is ProgressSafety.STALE:
            return self.position_seconds
        elapsed = now - self.observed_at if self.playing else 0.0
        return min(self.duration_seconds, self.position_seconds + elapsed)


@dataclass(frozen=True)
class ProgressSafetyProof:
    safety: ProgressSafety
    bounded: bool
    reason: str
    video_ref: str | None = None
    observed_age_seconds: float | None = None
    estimated_position_seconds: float | None = None


@dataclass(frozen=True)
class YouTubeButtonEvent:
    button: YouTubeButton
    phase: YouTubePhase
    pointer_id: str = "primary"
    progress: YouTubeProgressContext | None = None


@dataclass(frozen=True)
class YouTubeBrokerCommand:
    action: str
    button: YouTubeButton
    phase: YouTubePhase
    pointer_id: str
    progress_proof: ProgressSafetyProof


class YouTubeBrokerSink(Protocol):
    def send(self, command: YouTubeBrokerCommand) -> None: ...


@dataclass
class RecordingYouTubeBrokerSink:
    commands: list[YouTubeBrokerCommand] = field(default_factory=list)

    def send(self, command: YouTubeBrokerCommand) -> None:
        self.commands.append(command)


@dataclass
class YouTubeRemoteBroker:
    sink: YouTubeBrokerSink
    mapping: Mapping[YouTubeButton, str] = field(default_factory=lambda: dict(YOUTUBE_BROKER_MAPPING))
    max_progress_age_seconds: float = MAX_PROGRESS_AGE_SECONDS

    def dispatch(self, event: YouTubeButtonEvent, *, now: float | None = None) -> tuple[YouTubeBrokerCommand, ...]:
        timestamp = monotonic() if now is None else now
        self._validate_event(event)
        proof = self._progress_proof(event, now=timestamp)
        command = YouTubeBrokerCommand(
            action=self.mapping[event.button],
            button=event.button,
            phase=event.phase,
            pointer_id=event.pointer_id,
            progress_proof=proof,
        )
        self.sink.send(command)
        return (command,)

    def _validate_event(self, event: YouTubeButtonEvent) -> None:
        if event.button not in YOUTUBE_BUTTON_ALLOWLIST:
            raise YouTubeRemoteContractError("button is not allowlisted")
        if event.phase not in YOUTUBE_PHASE_ALLOWLIST:
            raise YouTubeRemoteContractError("phase is not allowlisted")
        if event.button not in self.mapping:
            raise YouTubeRemoteContractError("button has no bounded broker mapping")
        if not event.pointer_id.strip():
            raise YouTubeRemoteContractError("pointer_id must be non-empty")

    def _progress_proof(self, event: YouTubeButtonEvent, *, now: float) -> ProgressSafetyProof:
        if event.button not in PROGRESS_AWARE_BUTTONS:
            return ProgressSafetyProof(
                safety=ProgressSafety.ABSENT,
                bounded=True,
                reason="button does not require playback progress",
            )
        if event.progress is None:
            return ProgressSafetyProof(
                safety=ProgressSafety.ABSENT,
                bounded=True,
                reason="progress-aware command carries no optimistic position context",
            )
        safety = event.progress.validate(now=now, max_age_seconds=self.max_progress_age_seconds)
        age = now - event.progress.observed_at
        estimated = event.progress.estimated_position(now=now, max_age_seconds=self.max_progress_age_seconds)
        return ProgressSafetyProof(
            safety=safety,
            bounded=True,
            reason=(
                "fresh progress context may be used for UI feedback only"
                if safety is ProgressSafety.FRESH
                else "stale progress context is preserved as evidence and must not drive optimistic seek state"
            ),
            video_ref=event.progress.video_ref,
            observed_age_seconds=age,
            estimated_position_seconds=estimated,
        )


def validate_youtube_remote_contract() -> None:
    if set(YOUTUBE_BROKER_MAPPING) != set(YOUTUBE_BUTTON_ALLOWLIST):
        raise YouTubeRemoteContractError("broker mapping must exactly equal the button allowlist")
    if not PROGRESS_AWARE_BUTTONS <= YOUTUBE_BUTTON_ALLOWLIST:
        raise YouTubeRemoteContractError("progress-aware buttons must stay inside the allowlist")
    if any(action.startswith(("ssh", "adb", "xdotool", "input keyevent")) for action in YOUTUBE_BROKER_MAPPING.values()):
        raise YouTubeRemoteContractError("broker mapping must not contain live input commands")
