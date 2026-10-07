from __future__ import annotations

import pytest

from core.exo_device_control_plane import (
    BoundedRecoveryPolicy,
    ExoCommand,
    ExoControlError,
    PerDeviceTransportSupervisor,
    StaticTransport,
    TransportRequest,
    TransportResult,
    VerificationState,
    canonical_exo_control_plane,
    canonical_exo_registry,
)


class _FlakyTransport:
    def __init__(self, transport_id: str, states: list[VerificationState]) -> None:
        self.transport_id = transport_id
        self.states = list(states)
        self.calls = 0

    def execute(self, request: TransportRequest) -> TransportResult:
        self.calls += 1
        state = self.states.pop(0)
        return TransportResult(state=state, reason=f"{self.transport_id}:{state.value}", payload={"call": self.calls})


def test_canonical_registry_has_stable_separate_device_identities() -> None:
    registry = canonical_exo_registry()
    payload = registry.to_mapping()
    devices = {item["device_id"]: item for item in payload["devices"]}

    assert payload["schema"] == "skeleton.exo.device_control_plane.v1"
    assert set(devices) == {"home_edge_01", "samsung_tv", "sharp_tv_living_room"}
    assert devices["home_edge_01"]["adapter_kind"] == "home_edge"
    assert devices["samsung_tv"]["adapter_kind"] == "samsung_adb"
    assert devices["sharp_tv_living_room"]["adapter_kind"] == "sharp_ir"
    assert devices["home_edge_01"]["portable_role"] is True
    assert devices["home_edge_01"]["current_host"] == "home-edge-01"


def test_capability_driven_adapters_do_not_flatten_heterogeneous_devices() -> None:
    plane = canonical_exo_control_plane()

    samsung = plane.execute(ExoCommand(device_id="samsung_tv", capability="adb.watchdog"))
    sharp = plane.execute(ExoCommand(device_id="sharp_tv_living_room", capability="ir.transmit"))

    assert samsung.state == VerificationState.DRY_RUN
    assert samsung.adapter_kind == "samsung_adb"
    assert samsung.observation.details["transport_candidates"] == ["samsung_usb_adb_watchdog"]
    assert sharp.state == VerificationState.DRY_RUN
    assert sharp.adapter_kind == "sharp_ir"
    assert sharp.observation.details["transport_candidates"] == ["android_consumer_ir"]
    with pytest.raises(ExoControlError, match="device_capability_unsupported"):
        plane.execute(ExoCommand(device_id="sharp_tv_living_room", capability="adb.watchdog"))


def test_dry_run_truthfully_reports_not_sent_to_transport() -> None:
    transport = _FlakyTransport("primary", [VerificationState.VERIFIED])
    plane = canonical_exo_control_plane(transports={"home_edge_01": (transport,)})

    result = plane.execute(ExoCommand(device_id="home_edge_01", capability="media.play", dry_run=True))

    assert result.state == VerificationState.DRY_RUN
    assert result.observation.transport_id is None
    assert result.observation.attempts == 0
    assert result.observation.reason == "dry_run_not_sent_to_transport"
    assert transport.calls == 0


def test_per_device_supervisor_bounds_recovery_and_uses_failover() -> None:
    primary = _FlakyTransport("primary", [VerificationState.RECOVERING])
    failover = _FlakyTransport("failover", [VerificationState.VERIFIED])
    supervisor = PerDeviceTransportSupervisor(
        device_id="samsung_tv",
        transports=(primary, failover),
        recovery_policy=BoundedRecoveryPolicy(max_attempts=2, allow_failover=True),
    )
    request = TransportRequest(
        command_id="cmd-1",
        device_id="samsung_tv",
        adapter_kind="samsung_adb",
        capability="adb.watchdog",
        payload={},
    )

    result, observation = supervisor.execute(request, dry_run=False)

    assert result.state == VerificationState.VERIFIED
    assert observation.transport_id == "failover"
    assert observation.attempts == 2
    assert observation.failover_count == 1
    assert observation.recovery_exhausted is False
    assert primary.calls == 1
    assert failover.calls == 1


def test_recovery_budget_exhaustion_is_reported_as_blocked() -> None:
    primary = _FlakyTransport("primary", [VerificationState.RECOVERING])
    failover = _FlakyTransport("failover", [VerificationState.RECOVERING])
    supervisor = PerDeviceTransportSupervisor(
        device_id="sharp_tv_living_room",
        transports=(primary, failover),
        recovery_policy=BoundedRecoveryPolicy(max_attempts=2, allow_failover=True),
    )
    request = TransportRequest(
        command_id="cmd-2",
        device_id="sharp_tv_living_room",
        adapter_kind="sharp_ir",
        capability="ir.transmit",
        payload={},
    )

    result, observation = supervisor.execute(request, dry_run=False)

    assert result.state == VerificationState.BLOCKED
    assert result.reason == "recovery_budget_exhausted"
    assert observation.recovery_exhausted is True
    assert observation.attempts == 2
    assert observation.failover_count == 1


def test_live_yellow_capability_requires_operator_approval_before_transport() -> None:
    transport = StaticTransport("sharp-transport")
    plane = canonical_exo_control_plane(transports={"sharp_tv_living_room": (transport,)})

    result = plane.execute(ExoCommand(device_id="sharp_tv_living_room", capability="ir.transmit", dry_run=False))

    assert result.state == VerificationState.BLOCKED
    assert result.observation.reason == "operator_approval_required"
    assert result.observation.attempts == 0


def test_approved_live_green_command_can_be_verified_by_transport() -> None:
    transport = StaticTransport("home-edge", response={"executor": "existing_home_edge_gateway"})
    plane = canonical_exo_control_plane(transports={"home_edge_01": (transport,)})

    result = plane.execute(ExoCommand(device_id="home_edge_01", capability="media.play", dry_run=False))

    assert result.state == VerificationState.VERIFIED
    assert result.response["executor"] == "existing_home_edge_gateway"
    assert result.observation.transport_id == "home-edge"
    assert result.observation.attempts == 1
