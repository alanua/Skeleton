from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.home_edge.anycubic_kobra import (
    OCTOPRINT_LXC_SPEC,
    TELEMETRY_CAPABILITIES,
    BedThermistorEvidence,
    EvidenceState,
    IdentityState,
    ThermistorHealth,
    UsbIdentityEvidence,
    evaluate_identity,
    evaluate_ntc_safety_gate,
)


NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


COMPLETE_PHYSICAL_VERIFICATION = {
    "stable_cold_ambient_observation": True,
    "stable_harness_bed_movement_observation": True,
    "controlled_heating_observation": True,
}


def _identity(**changes: object) -> UsbIdentityEvidence:
    values = {
        "manufacturer": "Anycubic",
        "model": "Kobra",
        "generation": "1st_gen",
        "release_year": 2022,
        "vid": "1a86",
        "pid": "7523",
        "serial": "SYNTHETIC-KOBRA-001",
    }
    values.update(changes)
    return UsbIdentityEvidence(**values)  # type: ignore[arg-type]


def test_exact_kobra_identity_accepts_stable_usb_tuple_or_by_id() -> None:
    tuple_identity = evaluate_identity(_identity())
    by_id_identity = evaluate_identity(
        _identity(vid=None, pid=None, serial=None, by_id_path="/dev/serial/by-id/usb-synthetic-kobra")
    )

    assert tuple_identity.state is IdentityState.VERIFIED
    assert tuple_identity.stable_hardware_key == "usb:1a86:7523:SYNTHETIC-KOBRA-001"
    assert by_id_identity.state is IdentityState.VERIFIED


@pytest.mark.parametrize("model", ["Vyper", "vyper", "VYPER"])
def test_vyper_is_always_rejected(model: str) -> None:
    identity = evaluate_identity(_identity(model=model))
    assert identity.state is IdentityState.REJECTED
    assert identity.reason == "vyper_is_not_an_active_device"


def test_identity_is_pending_without_stable_usb_evidence() -> None:
    assert evaluate_identity(None).state is IdentityState.IDENTITY_PENDING
    identity = evaluate_identity(_identity(vid="", pid=None, serial=None))
    assert identity.state is IdentityState.IDENTITY_PENDING
    assert identity.stable_hardware_key is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("manufacturer", "Other"), ("model", "Kobra 2"), ("generation", "2nd_gen"), ("release_year", 2023)],
)
def test_nearby_but_non_exact_devices_are_rejected(field: str, value: object) -> None:
    assert evaluate_identity(_identity(**{field: value})).state is IdentityState.REJECTED


def test_lxc_defaults_are_minimal_local_and_stable_usb_only() -> None:
    assert OCTOPRINT_LXC_SPEC.host == "brandenburg_proxmox"
    assert OCTOPRINT_LXC_SPEC.os == "Debian 12"
    assert (OCTOPRINT_LXC_SPEC.vcpus, OCTOPRINT_LXC_SPEC.memory_mib, OCTOPRINT_LXC_SPEC.rootfs_gib) == (1, 1024, 8)
    assert OCTOPRINT_LXC_SPEC.network_scope == "local_only"
    assert OCTOPRINT_LXC_SPEC.usb_device_path_policy == "stable_dev_serial_by_id_required"
    assert OCTOPRINT_LXC_SPEC.wait_for_physical_connection is True
    assert OCTOPRINT_LXC_SPEC.firmware == "factory"
    assert OCTOPRINT_LXC_SPEC.klipper_enabled is False


def test_telemetry_capabilities_are_read_only() -> None:
    assert {capability.name for capability in TELEMETRY_CAPABILITIES} == {
        "connection_state",
        "nozzle_temperature",
        "bed_temperature",
        "job_progress",
        "job_history",
    }
    assert all(capability.read_only for capability in TELEMETRY_CAPABILITIES)


@pytest.mark.parametrize(
    "state",
    [EvidenceState.SENT, EvidenceState.ACCEPTED, EvidenceState.APPLIED],
)
def test_nonphysical_ntc_states_cannot_enable_print_or_leveling(state: EvidenceState) -> None:
    decision = evaluate_ntc_safety_gate(
        BedThermistorEvidence(state, ThermistorHealth.HEALTHY, NOW), now=NOW
    )
    assert not decision.autonomous_print_start_allowed
    assert not decision.unattended_print_start_allowed
    assert not decision.bed_leveling_allowed


def test_unresolved_abnormal_ntc_denies_print_and_leveling() -> None:
    decision = evaluate_ntc_safety_gate(
        BedThermistorEvidence(
            EvidenceState.PHYSICALLY_VERIFIED,
            ThermistorHealth.ABNORMAL,
            NOW,
            **COMPLETE_PHYSICAL_VERIFICATION,
        ),
        now=NOW,
    )
    assert decision.reason == "hotbed_ntc_abnormal"
    assert not any(
        (
            decision.autonomous_print_start_allowed,
            decision.unattended_print_start_allowed,
            decision.bed_leveling_allowed,
        )
    )


@pytest.mark.parametrize(
    "missing_component",
    tuple(COMPLETE_PHYSICAL_VERIFICATION),
)
def test_each_missing_physical_verification_component_fails_closed(
    missing_component: str,
) -> None:
    coverage = dict(COMPLETE_PHYSICAL_VERIFICATION)
    coverage[missing_component] = False
    decision = evaluate_ntc_safety_gate(
        BedThermistorEvidence(
            EvidenceState.PHYSICALLY_VERIFIED,
            ThermistorHealth.HEALTHY,
            NOW,
            **coverage,
        ),
        now=NOW,
    )

    assert decision.reason == "bed_thermistor_physical_verification_incomplete"
    assert not any(
        (
            decision.autonomous_print_start_allowed,
            decision.unattended_print_start_allowed,
            decision.bed_leveling_allowed,
        )
    )


def test_static_reading_labeled_physically_verified_cannot_clear_gate() -> None:
    decision = evaluate_ntc_safety_gate(
        BedThermistorEvidence(
            EvidenceState.PHYSICALLY_VERIFIED,
            ThermistorHealth.HEALTHY,
            NOW,
        ),
        now=NOW,
    )

    assert decision.reason == "bed_thermistor_physical_verification_incomplete"
    assert not decision.bed_leveling_allowed


def test_only_fresh_complete_physically_verified_healthy_evidence_clears_gate() -> None:
    decision = evaluate_ntc_safety_gate(
        BedThermistorEvidence(
            EvidenceState.PHYSICALLY_VERIFIED,
            ThermistorHealth.HEALTHY,
            NOW,
            **COMPLETE_PHYSICAL_VERIFICATION,
        ),
        now=NOW,
    )
    assert decision.autonomous_print_start_allowed
    assert decision.unattended_print_start_allowed
    assert decision.bed_leveling_allowed


def test_missing_and_stale_evidence_fail_closed() -> None:
    missing = evaluate_ntc_safety_gate(None, now=NOW)
    stale = evaluate_ntc_safety_gate(
        BedThermistorEvidence(
            EvidenceState.PHYSICALLY_VERIFIED,
            ThermistorHealth.HEALTHY,
            NOW - timedelta(hours=25),
            **COMPLETE_PHYSICAL_VERIFICATION,
        ),
        now=NOW,
    )
    assert missing.reason == "bed_thermistor_evidence_missing"
    assert stale.reason == "bed_thermistor_evidence_stale"
    assert not missing.autonomous_print_start_allowed
    assert not stale.bed_leveling_allowed
