from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol


CAPABILITY = "youtube_tv_receiver"
ORDINARY_YOUTUBE_WEB = "youtube_web"
ORDINARY_LOCAL_PLAYER = "mpv"
DISPLAY_OWNER_RECEIVER = "youtube_tv_receiver"
DISPLAY_OWNER_DESKTOP = "chrome_mpv_stack"
OFFICIAL_YOUTUBE_TV_PACKAGE = "com.google.android.youtube.tv"
WAYDROID_SURFACE = "waydroid_android_tv_youtube"


class ReceiverReason(StrEnum):
    AVAILABLE = "available"
    RECEIVER_UNAVAILABLE = "receiver_unavailable"
    RECEIVER_RUNNING = "receiver_running"
    RECEIVER_STOPPED = "receiver_stopped"
    DUPLICATE_RECEIVER_INSTANCES = "duplicate_receiver_instances"
    ADB_CLOSED = "adb_closed"


@dataclass(frozen=True)
class ReceiverState:
    active_mode: str = ORDINARY_YOUTUBE_WEB
    previous_mode: str = ORDINARY_YOUTUBE_WEB
    receiver_instance_id: str | None = None
    volume_level: int | None = None


@dataclass(frozen=True)
class ReceiverStatus:
    receiver_available: bool
    receiver_running: bool
    pairing_ready: bool
    display_owner: str | None
    stable_reason: str

    def public_status(self) -> dict[str, object]:
        return {
            "receiver_available": self.receiver_available,
            "receiver_running": self.receiver_running,
            "pairing_ready": self.pairing_ready,
            "display_owner": self.display_owner,
            "stable_reason": self.stable_reason,
        }


@dataclass(frozen=True)
class ReceiverTransition:
    state: ReceiverState
    status: ReceiverStatus
    actions: tuple[str, ...]
    idempotent: bool
    adb_closed: bool
    volume_preserved: bool


class YouTubeTvReceiverBackend(Protocol):
    def receiver_available(self) -> bool: ...

    def receiver_instance_count(self) -> int: ...

    def launch_official_youtube_tv(self, *, package_name: str, surface: str) -> str: ...

    def stop_receiver(self, *, instance_id: str | None) -> None: ...

    def set_display_owner(self, owner: str | None) -> None: ...

    def restore_chrome_mpv_stack(self, *, previous_mode: str, preserve_volume: bool) -> None: ...

    def pairing_ready(self) -> bool: ...

    def adb_server_running(self) -> bool: ...

    def stop_adb_server(self) -> None: ...


def receiver_status(backend: YouTubeTvReceiverBackend) -> ReceiverStatus:
    available = backend.receiver_available()
    count = backend.receiver_instance_count() if available else 0
    if count > 1:
        return ReceiverStatus(available, True, False, DISPLAY_OWNER_RECEIVER, ReceiverReason.DUPLICATE_RECEIVER_INSTANCES.value)
    return ReceiverStatus(
        receiver_available=available,
        receiver_running=count == 1,
        pairing_ready=bool(available and count == 1 and backend.pairing_ready()),
        display_owner=DISPLAY_OWNER_RECEIVER if count == 1 else None,
        stable_reason=(ReceiverReason.AVAILABLE.value if available else ReceiverReason.RECEIVER_UNAVAILABLE.value),
    )


def enter_receiver(state: ReceiverState, backend: YouTubeTvReceiverBackend) -> ReceiverTransition:
    if not backend.receiver_available():
        return ReceiverTransition(
            state=state,
            status=ReceiverStatus(False, False, False, None, ReceiverReason.RECEIVER_UNAVAILABLE.value),
            actions=(),
            idempotent=True,
            adb_closed=_close_adb_if_running(backend),
            volume_preserved=True,
        )

    count = backend.receiver_instance_count()
    if count > 1:
        return ReceiverTransition(
            state=state,
            status=ReceiverStatus(True, True, False, DISPLAY_OWNER_RECEIVER, ReceiverReason.DUPLICATE_RECEIVER_INSTANCES.value),
            actions=(),
            idempotent=True,
            adb_closed=_close_adb_if_running(backend),
            volume_preserved=True,
        )

    actions: list[str] = []
    instance_id = state.receiver_instance_id
    idempotent = state.active_mode == CAPABILITY and count == 1
    previous_mode = state.previous_mode if state.active_mode == CAPABILITY else state.active_mode
    if count == 0:
        instance_id = backend.launch_official_youtube_tv(
            package_name=OFFICIAL_YOUTUBE_TV_PACKAGE,
            surface=WAYDROID_SURFACE,
        )
        actions.append("receiver.launch_official_youtube_tv")

    backend.set_display_owner(DISPLAY_OWNER_RECEIVER)
    actions.append("display.owner.youtube_tv_receiver")
    adb_closed = _close_adb_if_running(backend)
    return ReceiverTransition(
        state=replace(
            state,
            active_mode=CAPABILITY,
            previous_mode=previous_mode,
            receiver_instance_id=instance_id,
        ),
        status=ReceiverStatus(True, True, backend.pairing_ready(), DISPLAY_OWNER_RECEIVER, ReceiverReason.RECEIVER_RUNNING.value),
        actions=tuple(actions),
        idempotent=idempotent,
        adb_closed=adb_closed,
        volume_preserved=True,
    )


def exit_receiver(state: ReceiverState, backend: YouTubeTvReceiverBackend) -> ReceiverTransition:
    actions: list[str] = []
    if state.receiver_instance_id is not None or backend.receiver_instance_count() == 1:
        backend.stop_receiver(instance_id=state.receiver_instance_id)
        actions.append("receiver.stop")
    backend.restore_chrome_mpv_stack(previous_mode=state.previous_mode, preserve_volume=True)
    actions.append("desktop.restore_chrome_mpv")
    backend.set_display_owner(DISPLAY_OWNER_DESKTOP)
    actions.append("display.owner.chrome_mpv_stack")
    adb_closed = _close_adb_if_running(backend)
    return ReceiverTransition(
        state=replace(state, active_mode=state.previous_mode, receiver_instance_id=None),
        status=ReceiverStatus(
            backend.receiver_available(),
            False,
            False,
            DISPLAY_OWNER_DESKTOP,
            ReceiverReason.RECEIVER_STOPPED.value,
        ),
        actions=tuple(actions),
        idempotent=state.active_mode != CAPABILITY and state.receiver_instance_id is None,
        adb_closed=adb_closed,
        volume_preserved=True,
    )


def _close_adb_if_running(backend: YouTubeTvReceiverBackend) -> bool:
    if backend.adb_server_running():
        backend.stop_adb_server()
    return not backend.adb_server_running()
