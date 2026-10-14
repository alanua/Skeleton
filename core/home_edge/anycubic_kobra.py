"""Public-safe, declarative contract for the 2022 Anycubic Kobra.

This module deliberately contains no device, network, hypervisor, or Home
Assistant clients.  It only describes the intended integration and evaluates
synthetic evidence supplied by a caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Final


TARGET_MANUFACTURER: Final = "Anycubic"
TARGET_MODEL: Final = "Kobra"
TARGET_GENERATION: Final = "1st_gen"
TARGET_RELEASE_YEAR: Final = 2022
EXCLUDED_MODEL: Final = "Vyper"
STABLE_SERIAL_PREFIX: Final = "/dev/serial/by-id/"


class IdentityState(str, Enum):
    IDENTITY_PENDING = "identity_pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


@dataclass(frozen=True)
class UsbIdentityEvidence:
    """Observed USB identity; values are evidence, never discovery instructions."""

    manufacturer: str
    model: str
    generation: str
    release_year: int
    vid: str | None = None
    pid: str | None = None
    serial: str | None = None
    by_id_path: str | None = None

    @property
    def has_stable_hardware_key(self) -> bool:
        complete_usb_tuple = all(
            value is not None and bool(value.strip())
            for value in (self.vid, self.pid, self.serial)
        )
        stable_by_id = bool(
            self.by_id_path
            and self.by_id_path.startswith(STABLE_SERIAL_PREFIX)
            and len(self.by_id_path) > len(STABLE_SERIAL_PREFIX)
        )
        return complete_usb_tuple or stable_by_id


@dataclass(frozen=True)
class DeviceIdentity:
    manufacturer: str = TARGET_MANUFACTURER
    model: str = TARGET_MODEL
    generation: str = TARGET_GENERATION
    release_year: int = TARGET_RELEASE_YEAR
    state: IdentityState = IdentityState.IDENTITY_PENDING
    stable_hardware_key: str | None = None
    reason: str = "stable_usb_identity_not_observed"


def evaluate_identity(evidence: UsbIdentityEvidence | None) -> DeviceIdentity:
    """Resolve only the exact 2022 first-generation Kobra, failing closed."""

    if evidence is None:
        return DeviceIdentity()
    if evidence.model.casefold() == EXCLUDED_MODEL.casefold():
        return DeviceIdentity(
            state=IdentityState.REJECTED,
            reason="vyper_is_not_an_active_device",
        )
    exact_target = (
        evidence.manufacturer == TARGET_MANUFACTURER
        and evidence.model == TARGET_MODEL
        and evidence.generation == TARGET_GENERATION
        and evidence.release_year == TARGET_RELEASE_YEAR
    )
    if not exact_target:
        return DeviceIdentity(
            state=IdentityState.REJECTED,
            reason="not_exact_2022_kobra_first_generation",
        )
    if not evidence.has_stable_hardware_key:
        return DeviceIdentity(reason="stable_usb_identity_not_observed")
    hardware_key = evidence.by_id_path or (
        f"usb:{evidence.vid}:{evidence.pid}:{evidence.serial}"
    )
    return DeviceIdentity(
        state=IdentityState.VERIFIED,
        stable_hardware_key=hardware_key,
        reason="stable_usb_identity_observed",
    )


@dataclass(frozen=True)
class OctoPrintLxcSpec:
    host: str = "brandenburg_proxmox"
    os: str = "Debian 12"
    vcpus: int = 1
    memory_mib: int = 1024
    rootfs_gib: int = 8
    network_scope: str = "local_only"
    usb_device_path_policy: str = "stable_dev_serial_by_id_required"
    wait_for_physical_connection: bool = True
    firmware: str = "factory"
    klipper_enabled: bool = False


OCTOPRINT_LXC_SPEC: Final = OctoPrintLxcSpec()


@dataclass(frozen=True)
class TelemetryCapability:
    name: str
    read_only: bool = True


TELEMETRY_CAPABILITIES: Final = (
    TelemetryCapability("connection_state"),
    TelemetryCapability("nozzle_temperature"),
    TelemetryCapability("bed_temperature"),
    TelemetryCapability("job_progress"),
    TelemetryCapability("job_history"),
)


class EvidenceState(str, Enum):
    SENT = "sent"
    ACCEPTED = "accepted"
    APPLIED = "applied"
    PHYSICALLY_VERIFIED = "physically_verified"


class ThermistorHealth(str, Enum):
    HEALTHY = "healthy"
    ABNORMAL = "abnormal"


@dataclass(frozen=True)
class BedThermistorEvidence:
    state: EvidenceState
    health: ThermistorHealth
    observed_at: datetime
    stable_cold_ambient_observation: bool = False
    stable_harness_bed_movement_observation: bool = False
    controlled_heating_observation: bool = False

    @property
    def has_complete_physical_verification(self) -> bool:
        """Require every distinct observation; a single static reading is insufficient."""

        return all(
            observation is True
            for observation in (
                self.stable_cold_ambient_observation,
                self.stable_harness_bed_movement_observation,
                self.controlled_heating_observation,
            )
        )


@dataclass(frozen=True)
class SafetyDecision:
    autonomous_print_start_allowed: bool
    unattended_print_start_allowed: bool
    bed_leveling_allowed: bool
    reason: str


DEFAULT_EVIDENCE_MAX_AGE: Final = timedelta(hours=24)


def evaluate_ntc_safety_gate(
    evidence: BedThermistorEvidence | None,
    *,
    now: datetime | None = None,
    max_age: timedelta = DEFAULT_EVIDENCE_MAX_AGE,
) -> SafetyDecision:
    """Permit hazardous operations only after fresh physical verification."""

    denied = (False, False, False)
    if evidence is None:
        return SafetyDecision(*denied, reason="bed_thermistor_evidence_missing")
    if evidence.observed_at.tzinfo is None:
        return SafetyDecision(*denied, reason="bed_thermistor_evidence_stale")
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    age = checked_at - evidence.observed_at
    if age < timedelta(0) or age > max_age:
        return SafetyDecision(*denied, reason="bed_thermistor_evidence_stale")
    if evidence.state is not EvidenceState.PHYSICALLY_VERIFIED:
        return SafetyDecision(*denied, reason="bed_thermistor_not_physically_verified")
    if evidence.health is not ThermistorHealth.HEALTHY:
        return SafetyDecision(*denied, reason="hotbed_ntc_abnormal")
    if not evidence.has_complete_physical_verification:
        return SafetyDecision(
            *denied,
            reason="bed_thermistor_physical_verification_incomplete",
        )
    return SafetyDecision(
        True,
        True,
        True,
        reason="healthy_bed_thermistor_physical_verification_complete",
    )
