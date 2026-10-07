from __future__ import annotations

import pytest

from core.exo_device_control_plane import (
    BoundedRecoveryPolicy,
    CapabilityDomain,
    ExoCommand,
    ExoCapability,
    ExoControlError,
    ExoDevice,
    ExoDeviceRegistry,
    PerDeviceTransportSupervisor,
    RiskLevel,
    StableDeviceIdentity,
    StaticTransport,
    TransportRequest,
    TransportResult,
    VerificationState,
    canonical_exo_control_plane,
    canonical_exo_registry,
)


HOME_EDGE_ID = "home_edge.home_edge_01"
SAMSUNG_ID = "display.samsung_tv"
SHARP_ID = "display.sharp_tv_living_room"


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
    assert {HOME_EDGE_ID, SAMSUNG_ID, SHARP_ID} <= set(devices)
    assert len(devices) == len(payload["devices"])
    assert devices[HOME_EDGE_ID]["adapter_kind"] == "home_edge"
    assert devices[SAMSUNG_ID]["adapter_kind"] == "samsung_adb"
    assert devices[SHARP_ID]["adapter_kind"] == "sharp_ir"
    assert devices[HOME_EDGE_ID]["portable_role"] is True
    assert devices[HOME_EDGE_ID]["current_host"] == "home-edge-01"
    assert "home_edge_01" in devices[HOME_EDGE_ID]["registry_aliases"]
    assert "samsung_tv" in devices[SAMSUNG_ID]["registry_aliases"]
    assert "sharp_tv_living_room" in devices[SHARP_ID]["registry_aliases"]


def test_legacy_aliases_resolve_to_canonical_device_ids() -> None:
    plane = canonical_exo_control_plane()

    result = plane.execute(ExoCommand(device_id="samsung_tv", capability="adb.watchdog"))

    assert result.device_id == SAMSUNG_ID
    assert result.observation.device_id == SAMSUNG_ID


def test_capability_driven_adapters_do_not_flatten_heterogeneous_devices() -> None:
    plane = canonical_exo_control_plane()

    samsung = plane.execute(ExoCommand(device_id=SAMSUNG_ID, capability="adb.watchdog"))
    sharp = plane.execute(ExoCommand(device_id=SHARP_ID, capability="ir.transmit"))

    assert samsung.state == VerificationState.DRY_RUN
    assert samsung.adapter_kind == "samsung_adb"
    assert samsung.observation.details["transport_candidates"] == ["samsung_usb_adb_watchdog"]
    assert sharp.state == VerificationState.DRY_RUN
    assert sharp.adapter_kind == "sharp_ir"
    assert sharp.observation.details["transport_candidates"] == ["android_consumer_ir"]
    with pytest.raises(ExoControlError, match="device_capability_unsupported"):
        plane.execute(ExoCommand(device_id=SHARP_ID, capability="adb.watchdog"))


def test_registry_drives_n_device_supervisors_without_fixed_three_device_authority() -> None:
    registry = ExoDeviceRegistry(
        devices=(
            *canonical_exo_registry().devices,
            ExoDevice(
                identity=StableDeviceIdentity(
                    device_id="display.den_tv",
                    adapter_kind="sharp_ir",
                    vendor="Sharp",
                    model="IR television",
                    location="den",
                ),
                capabilities=(ExoCapability("ir.transmit", CapabilityDomain.DEVICE_CONTROL, RiskLevel.YELLOW),),
                transport_ids=("den_consumer_ir",),
            ),
        )
    )
    plane = canonical_exo_control_plane(registry=registry)

    result = plane.execute(ExoCommand(device_id="display.den_tv", capability="ir.transmit"))

    assert result.state == VerificationState.DRY_RUN
    assert result.adapter_kind == "sharp_ir"
    assert result.observation.details["transport_candidates"] == ["den_consumer_ir"]


def test_explicitly_unconfigured_device_fails_closed_before_transport() -> None:
    plane = canonical_exo_control_plane(transports={SAMSUNG_ID: ()})

    result = plane.execute(ExoCommand(device_id=SAMSUNG_ID, capability="adb.watchdog"))

    assert result.state == VerificationState.BLOCKED
    assert result.observation.reason == "transport_supervisor_missing"


def test_dry_run_truthfully_reports_not_sent_to_transport() -> None:
    transport = _FlakyTransport("primary", [VerificationState.VERIFIED])
    plane = canonical_exo_control_plane(transports={HOME_EDGE_ID: (transport,)})

    result = plane.execute(ExoCommand(device_id=HOME_EDGE_ID, capability="media.play", dry_run=True))

    assert result.state == VerificationState.DRY_RUN
    assert result.observation.transport_id is None
    assert result.observation.attempts == 0
    assert result.observation.reason == "dry_run_not_sent_to_transport"
    assert transport.calls == 0


def test_per_device_supervisor_bounds_recovery_and_uses_failover() -> None:
    primary = _FlakyTransport("primary", [VerificationState.RECOVERING])
    failover = _FlakyTransport("failover", [VerificationState.VERIFIED])
    supervisor = PerDeviceTransportSupervisor(
        device_id=SAMSUNG_ID,
        transports=(primary, failover),
        recovery_policy=BoundedRecoveryPolicy(max_attempts=2, allow_failover=True),
    )
    request = TransportRequest(
        command_id="cmd-1",
        device_id=SAMSUNG_ID,
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
        device_id=SHARP_ID,
        transports=(primary, failover),
        recovery_policy=BoundedRecoveryPolicy(max_attempts=2, allow_failover=True),
    )
    request = TransportRequest(
        command_id="cmd-2",
        device_id=SHARP_ID,
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
    plane = canonical_exo_control_plane(transports={SHARP_ID: (transport,)})

    result = plane.execute(ExoCommand(device_id=SHARP_ID, capability="ir.transmit", dry_run=False))

    assert result.state == VerificationState.BLOCKED
    assert result.observation.reason == "operator_approval_required"
    assert result.observation.attempts == 0


def test_approved_live_green_command_can_be_verified_by_transport() -> None:
    transport = StaticTransport("home-edge", response={"executor": "existing_home_edge_gateway"})
    plane = canonical_exo_control_plane(transports={HOME_EDGE_ID: (transport,)})

    result = plane.execute(ExoCommand(device_id=HOME_EDGE_ID, capability="media.play", dry_run=False))

    assert result.state == VerificationState.VERIFIED
    assert result.response["executor"] == "existing_home_edge_gateway"
    assert result.observation.transport_id == "home-edge"
    assert result.observation.attempts == 1
