from __future__ import annotations

from dataclasses import dataclass, field

from core.home_edge import youtube_tv_receiver as receiver


@dataclass
class SyntheticReceiverBackend:
    available: bool = True
    instances: list[str] = field(default_factory=list)
    display_owner: str | None = None
    pairing: bool = True
    adb_running: bool = False
    restored: list[tuple[str, bool]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def receiver_available(self) -> bool:
        return self.available

    def receiver_instance_count(self) -> int:
        return len(self.instances)

    def launch_official_youtube_tv(self, *, package_name: str, surface: str) -> str:
        assert package_name == receiver.OFFICIAL_YOUTUBE_TV_PACKAGE
        assert surface == receiver.WAYDROID_SURFACE
        self.calls.append(f"launch:{package_name}:{surface}")
        instance_id = "receiver-1"
        self.instances.append(instance_id)
        self.adb_running = True
        return instance_id

    def stop_receiver(self, *, instance_id: str | None) -> None:
        self.calls.append(f"stop:{instance_id or 'current'}")
        self.instances.clear()

    def set_display_owner(self, owner: str | None) -> None:
        self.calls.append(f"display:{owner}")
        self.display_owner = owner

    def restore_chrome_mpv_stack(self, *, previous_mode: str, preserve_volume: bool) -> None:
        self.calls.append(f"restore:{previous_mode}:{preserve_volume}")
        self.restored.append((previous_mode, preserve_volume))

    def pairing_ready(self) -> bool:
        return self.pairing

    def adb_server_running(self) -> bool:
        return self.adb_running

    def stop_adb_server(self) -> None:
        self.calls.append("adb:kill-server")
        self.adb_running = False


def test_enter_starts_exactly_one_bounded_receiver_and_owns_display() -> None:
    backend = SyntheticReceiverBackend()

    transition = receiver.enter_receiver(receiver.ReceiverState(active_mode=receiver.ORDINARY_YOUTUBE_WEB), backend)

    assert backend.instances == ["receiver-1"]
    assert backend.display_owner == receiver.DISPLAY_OWNER_RECEIVER
    assert transition.state.active_mode == receiver.CAPABILITY
    assert transition.state.previous_mode == receiver.ORDINARY_YOUTUBE_WEB
    assert transition.status.public_status() == {
        "receiver_available": True,
        "receiver_running": True,
        "pairing_ready": True,
        "display_owner": receiver.DISPLAY_OWNER_RECEIVER,
        "stable_reason": receiver.ReceiverReason.RECEIVER_RUNNING.value,
    }
    assert transition.adb_closed is True
    assert "adb:kill-server" in backend.calls


def test_repeated_enter_is_idempotent_and_does_not_launch_duplicate_receiver() -> None:
    backend = SyntheticReceiverBackend(instances=["receiver-1"])
    state = receiver.ReceiverState(active_mode=receiver.CAPABILITY, previous_mode=receiver.ORDINARY_YOUTUBE_WEB, receiver_instance_id="receiver-1")

    transition = receiver.enter_receiver(state, backend)

    assert transition.idempotent is True
    assert backend.instances == ["receiver-1"]
    assert not any(call.startswith("launch:") for call in backend.calls)
    assert transition.status.receiver_running is True


def test_duplicate_receiver_instances_are_public_stable_reason_not_new_launch() -> None:
    backend = SyntheticReceiverBackend(instances=["receiver-1", "receiver-2"])

    transition = receiver.enter_receiver(receiver.ReceiverState(), backend)

    assert transition.status.stable_reason == receiver.ReceiverReason.DUPLICATE_RECEIVER_INSTANCES.value
    assert backend.instances == ["receiver-1", "receiver-2"]
    assert not any(call.startswith("launch:") for call in backend.calls)


def test_exit_restores_chrome_mpv_stack_and_preserves_volume() -> None:
    backend = SyntheticReceiverBackend(instances=["receiver-1"], display_owner=receiver.DISPLAY_OWNER_RECEIVER)
    state = receiver.ReceiverState(
        active_mode=receiver.CAPABILITY,
        previous_mode=receiver.ORDINARY_LOCAL_PLAYER,
        receiver_instance_id="receiver-1",
        volume_level=37,
    )

    transition = receiver.exit_receiver(state, backend)

    assert backend.instances == []
    assert backend.restored == [(receiver.ORDINARY_LOCAL_PLAYER, True)]
    assert transition.state.active_mode == receiver.ORDINARY_LOCAL_PLAYER
    assert transition.state.volume_level == 37
    assert transition.volume_preserved is True
    assert transition.status.public_status() == {
        "receiver_available": True,
        "receiver_running": False,
        "pairing_ready": False,
        "display_owner": receiver.DISPLAY_OWNER_DESKTOP,
        "stable_reason": receiver.ReceiverReason.RECEIVER_STOPPED.value,
    }


def test_public_status_does_not_expose_private_pairing_or_account_values() -> None:
    backend = SyntheticReceiverBackend(instances=["receiver-1"], pairing=True)

    status = receiver.receiver_status(backend).public_status()

    assert set(status) == {
        "receiver_available",
        "receiver_running",
        "pairing_ready",
        "display_owner",
        "stable_reason",
    }
    assert "token" not in repr(status).lower()
    assert "account" not in repr(status).lower()
    assert "pairing_code" not in repr(status).lower()


def test_restart_restore_contract_keeps_private_profile_state_local() -> None:
    backend = SyntheticReceiverBackend(instances=["receiver-1"], pairing=True)
    restored = receiver.receiver_status(backend)

    assert restored.receiver_running is True
    assert restored.pairing_ready is True
    assert restored.display_owner == receiver.DISPLAY_OWNER_RECEIVER
    assert restored.public_status()["stable_reason"] == receiver.ReceiverReason.AVAILABLE.value
