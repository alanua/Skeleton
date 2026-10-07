from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Mapping, Protocol, Sequence
from uuid import uuid4


EXO_DEVICE_CONTROL_PLANE_SCHEMA = "skeleton.exo.device_control_plane.v1"
EXO_DEVICE_OBSERVABILITY_SCHEMA = "skeleton.exo.device_observability.v1"
CANONICAL_PLANE_ID = "exo-device-control-plane"


class ExoControlError(ValueError):
    """Raised when a device-control request is outside the EXO contract."""


class CapabilityDomain(StrEnum):
    CONNECTIVITY = "connectivity"
    DEVICE_CONTROL = "device_control"
    NETWORK_ADMIN = "network_admin"
    SERVICE_HUB = "service_hub"
    HUMAN_INTERFACE = "human_interface"
    PROVISIONING_RECOVERY = "provisioning_recovery"
    MEDIA_PRESENCE = "media_presence"


class RiskLevel(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class VerificationState(StrEnum):
    DRY_RUN = "dry_run"
    VERIFIED = "verified"
    RECOVERING = "recovering"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ExoCapability:
    name: str
    domain: CapabilityDomain
    risk: RiskLevel = RiskLevel.GREEN

    def to_mapping(self) -> dict[str, Any]:
        return {"name": self.name, "domain": self.domain.value, "risk": self.risk.value}


@dataclass(frozen=True)
class StableDeviceIdentity:
    device_id: str
    adapter_kind: str
    vendor: str
    model: str
    location: str | None = None
    registry_aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _safe_id(self.device_id):
            raise ExoControlError("device_id_invalid")
        if not self.adapter_kind.strip():
            raise ExoControlError("adapter_kind_required")
        if not self.vendor.strip():
            raise ExoControlError("device_vendor_required")
        if not self.model.strip():
            raise ExoControlError("device_model_required")

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "device_id": self.device_id,
            "adapter_kind": self.adapter_kind,
            "vendor": self.vendor,
            "model": self.model,
            "registry_aliases": list(self.registry_aliases),
        }
        if self.location:
            payload["location"] = self.location
        return payload


@dataclass(frozen=True)
class ExoDevice:
    identity: StableDeviceIdentity
    capabilities: tuple[ExoCapability, ...]
    transport_ids: tuple[str, ...]
    portable_role: bool = False
    current_host: str | None = None

    def __post_init__(self) -> None:
        if not self.capabilities:
            raise ExoControlError("device_capabilities_required")
        if not self.transport_ids:
            raise ExoControlError("device_transport_required")

    @property
    def device_id(self) -> str:
        return self.identity.device_id

    @property
    def adapter_kind(self) -> str:
        return self.identity.adapter_kind

    def capability(self, capability_name: str) -> ExoCapability | None:
        return next((item for item in self.capabilities if item.name == capability_name), None)

    def supports(self, capability_name: str) -> bool:
        return self.capability(capability_name) is not None

    def to_mapping(self) -> dict[str, Any]:
        payload = {
            **self.identity.to_mapping(),
            "capabilities": [capability.to_mapping() for capability in self.capabilities],
            "transport_ids": list(self.transport_ids),
            "portable_role": self.portable_role,
        }
        if self.current_host:
            payload["current_host"] = self.current_host
        return payload


@dataclass(frozen=True)
class ExoDeviceRegistry:
    devices: tuple[ExoDevice, ...]

    def __post_init__(self) -> None:
        ids = [device.device_id for device in self.devices]
        if len(ids) != len(set(ids)):
            raise ExoControlError("duplicate_device_id")

    def get(self, device_id: str) -> ExoDevice:
        for device in self.devices:
            if device.device_id == device_id or device_id in device.identity.registry_aliases:
                return device
        raise ExoControlError("unknown_device_id")

    def resolve(self, *, device_id: str, capability: str) -> tuple[ExoDevice, ExoCapability]:
        device = self.get(device_id)
        resolved = device.capability(capability)
        if resolved is None:
            raise ExoControlError("device_capability_unsupported")
        return device, resolved

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": EXO_DEVICE_CONTROL_PLANE_SCHEMA,
            "plane_id": CANONICAL_PLANE_ID,
            "devices": [device.to_mapping() for device in self.devices],
        }


@dataclass(frozen=True)
class ExoCommand:
    device_id: str
    capability: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    command_id: str = field(default_factory=lambda: f"exo-device-command-{uuid4()}")
    dry_run: bool = True
    operator_approval_ref: str | None = None


@dataclass(frozen=True)
class TransportRequest:
    command_id: str
    device_id: str
    adapter_kind: str
    capability: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class TransportResult:
    state: VerificationState
    reason: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeviceObservation:
    command_id: str
    device_id: str
    adapter_kind: str
    capability: str
    state: VerificationState
    reason: str
    transport_id: str | None
    attempts: int = 0
    failover_count: int = 0
    recovery_exhausted: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": EXO_DEVICE_OBSERVABILITY_SCHEMA,
            "plane_id": CANONICAL_PLANE_ID,
            "command_id": self.command_id,
            "device_id": self.device_id,
            "adapter_kind": self.adapter_kind,
            "capability": self.capability,
            "state": self.state.value,
            "reason": self.reason,
            "attempts": self.attempts,
            "failover_count": self.failover_count,
            "recovery_exhausted": self.recovery_exhausted,
            "details": dict(self.details),
        }
        if self.transport_id is not None:
            payload["transport_id"] = self.transport_id
        return payload


@dataclass(frozen=True)
class ControlResult:
    state: VerificationState
    command_id: str
    device_id: str
    capability: str
    adapter_kind: str
    response: Mapping[str, Any]
    observation: DeviceObservation

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": EXO_DEVICE_CONTROL_PLANE_SCHEMA,
            "plane_id": CANONICAL_PLANE_ID,
            "state": self.state.value,
            "command_id": self.command_id,
            "device_id": self.device_id,
            "capability": self.capability,
            "adapter_kind": self.adapter_kind,
            "response": dict(self.response),
            "observation": self.observation.to_mapping(),
        }


class DeviceTransport(Protocol):
    transport_id: str

    def execute(self, request: TransportRequest) -> TransportResult: ...


class DeviceAdapter(Protocol):
    adapter_kind: str

    def prepare(self, command: ExoCommand, device: ExoDevice, capability: ExoCapability) -> TransportRequest: ...


@dataclass(frozen=True)
class BoundedRecoveryPolicy:
    max_attempts: int = 2
    allow_failover: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ExoControlError("recovery_policy_requires_attempt")


class CapabilityDrivenDeviceAdapter:
    adapter_kind: str = "generic"

    def prepare(self, command: ExoCommand, device: ExoDevice, capability: ExoCapability) -> TransportRequest:
        if device.adapter_kind != self.adapter_kind:
            raise ExoControlError("adapter_device_kind_mismatch")
        if capability.name != command.capability:
            raise ExoControlError("adapter_capability_mismatch")
        payload = dict(command.payload)
        payload.setdefault("capability_domain", capability.domain.value)
        payload.setdefault("risk", capability.risk.value)
        return TransportRequest(
            command_id=command.command_id,
            device_id=device.device_id,
            adapter_kind=self.adapter_kind,
            capability=command.capability,
            payload=payload,
        )


class HomeEdgeDeviceAdapter(CapabilityDrivenDeviceAdapter):
    adapter_kind = "home_edge"


class SamsungDeviceAdapter(CapabilityDrivenDeviceAdapter):
    adapter_kind = "samsung_adb"


class SharpIrDeviceAdapter(CapabilityDrivenDeviceAdapter):
    adapter_kind = "sharp_ir"


@dataclass(frozen=True)
class StaticTransport:
    transport_id: str
    state: VerificationState = VerificationState.VERIFIED
    reason: str = "transport_verified"
    response: Mapping[str, Any] = field(default_factory=dict)

    def execute(self, request: TransportRequest) -> TransportResult:
        return TransportResult(
            state=self.state,
            reason=self.reason,
            payload={
                "device_id": request.device_id,
                "capability": request.capability,
                **dict(self.response),
            },
        )


class CallableTransport:
    def __init__(
        self,
        transport_id: str,
        executor: Callable[[TransportRequest], Mapping[str, Any]],
    ) -> None:
        self.transport_id = transport_id
        self._executor = executor

    def execute(self, request: TransportRequest) -> TransportResult:
        response = dict(self._executor(request))
        raw_state = response.get("state") or response.get("status") or VerificationState.VERIFIED.value
        state = _verification_state(raw_state)
        reason = str(response.get("reason") or "callable_transport_response")
        return TransportResult(state=state, reason=reason, payload=response)


class HomeEdgeExecTransport:
    """Adapter for the existing Home Edge executor gateway.

    This class deliberately delegates to the supplied executor callable. The
    EXO plane therefore supervises transport attempts but does not create a
    second command executor.
    """

    transport_id = "home_edge_exec_gateway"

    def __init__(self, executor: Callable[[Mapping[str, Any]], Any]) -> None:
        self._executor = executor

    def execute(self, request: TransportRequest) -> TransportResult:
        exec_request = request.payload.get("exec_request")
        if not isinstance(exec_request, Mapping):
            return TransportResult(
                state=VerificationState.BLOCKED,
                reason="home_edge_exec_request_missing",
                payload={"device_id": request.device_id},
            )
        receipt = self._executor(exec_request)
        mapped = receipt.to_mapping() if hasattr(receipt, "to_mapping") else dict(receipt)
        status = str(mapped.get("status", "")).lower()
        state = VerificationState.VERIFIED if status in {"ok", "done"} else VerificationState.BLOCKED
        return TransportResult(state=state, reason=f"home_edge_exec_{status or 'unknown'}", payload=mapped)


class PerDeviceTransportSupervisor:
    def __init__(
        self,
        *,
        device_id: str,
        transports: Sequence[DeviceTransport],
        recovery_policy: BoundedRecoveryPolicy | None = None,
    ) -> None:
        if not transports:
            raise ExoControlError("supervisor_requires_transport")
        self.device_id = device_id
        self.transports = tuple(transports)
        self.recovery_policy = recovery_policy or BoundedRecoveryPolicy()

    def execute(
        self,
        request: TransportRequest,
        *,
        dry_run: bool,
    ) -> tuple[TransportResult, DeviceObservation]:
        if request.device_id != self.device_id:
            raise ExoControlError("supervisor_device_mismatch")
        if dry_run:
            result = TransportResult(
                state=VerificationState.DRY_RUN,
                reason="dry_run_not_sent_to_transport",
                payload={
                    "device_id": request.device_id,
                    "capability": request.capability,
                    "transport_candidates": [transport.transport_id for transport in self.transports],
                },
            )
            return result, _observation_from_result(request, result, transport_id=None, attempts=0, failover_count=0)

        attempts = 0
        failover_count = 0
        last: tuple[TransportResult, str] | None = None
        for index, transport in enumerate(self.transports):
            if index > 0:
                if not self.recovery_policy.allow_failover:
                    break
                failover_count += 1
            attempts += 1
            try:
                result = transport.execute(request)
            except Exception as exc:  # noqa: BLE001 - supervisor must convert transport failures.
                result = TransportResult(
                    state=VerificationState.RECOVERING,
                    reason=f"transport_exception:{type(exc).__name__}",
                    payload={},
                )
            last = (result, transport.transport_id)
            if result.state is VerificationState.VERIFIED:
                return result, _observation_from_result(
                    request,
                    result,
                    transport_id=transport.transport_id,
                    attempts=attempts,
                    failover_count=failover_count,
                )
            if attempts >= self.recovery_policy.max_attempts:
                break

        if last is None:
            result = TransportResult(VerificationState.BLOCKED, "no_transport_attempted", {})
            transport_id = None
        else:
            result, transport_id = last
        terminal = result
        exhausted = attempts >= self.recovery_policy.max_attempts and result.state is not VerificationState.VERIFIED
        if exhausted and result.state is VerificationState.RECOVERING:
            terminal = TransportResult(VerificationState.BLOCKED, "recovery_budget_exhausted", result.payload)
        return terminal, _observation_from_result(
            request,
            terminal,
            transport_id=transport_id,
            attempts=attempts,
            failover_count=failover_count,
            recovery_exhausted=exhausted,
        )


class ExoDeviceControlPlane:
    def __init__(
        self,
        *,
        registry: ExoDeviceRegistry,
        adapters: Mapping[str, DeviceAdapter],
        supervisors: Mapping[str, PerDeviceTransportSupervisor],
    ) -> None:
        self.registry = registry
        self.adapters = dict(adapters)
        self.supervisors = dict(supervisors)

    def execute(self, command: ExoCommand) -> ControlResult:
        device, capability = self.registry.resolve(device_id=command.device_id, capability=command.capability)
        adapter = self.adapters.get(device.adapter_kind)
        if adapter is None:
            return _blocked_result(command, device, "adapter_missing", VerificationState.UNSUPPORTED)
        if not command.dry_run and capability.risk is not RiskLevel.GREEN and not command.operator_approval_ref:
            return _blocked_result(command, device, "operator_approval_required", VerificationState.BLOCKED)
        request = adapter.prepare(command, device, capability)
        supervisor = self.supervisors.get(device.device_id)
        if supervisor is None:
            return _blocked_result(command, device, "transport_supervisor_missing", VerificationState.BLOCKED)
        result, observation = supervisor.execute(request, dry_run=command.dry_run)
        return ControlResult(
            state=result.state,
            command_id=command.command_id,
            device_id=device.device_id,
            capability=command.capability,
            adapter_kind=device.adapter_kind,
            response=result.payload,
            observation=observation,
        )

    def public_registry(self) -> dict[str, Any]:
        return self.registry.to_mapping()


def canonical_exo_registry() -> ExoDeviceRegistry:
    return ExoDeviceRegistry(
        devices=(
            ExoDevice(
                identity=StableDeviceIdentity(
                    device_id="home_edge.home_edge_01",
                    adapter_kind="home_edge",
                    vendor="Skeleton",
                    model="Portable Home Edge role",
                    location="home_lan",
                    registry_aliases=("home_edge_01", "home-edge-01", "home-edge-role", "home_edge_tv"),
                ),
                current_host="home-edge-01",
                portable_role=True,
                capabilities=(
                    ExoCapability("connectivity.health", CapabilityDomain.CONNECTIVITY),
                    ExoCapability("ha.service_call", CapabilityDomain.DEVICE_CONTROL, RiskLevel.YELLOW),
                    ExoCapability("network.observe", CapabilityDomain.NETWORK_ADMIN),
                    ExoCapability("service.file_status", CapabilityDomain.SERVICE_HUB),
                    ExoCapability("ui.display", CapabilityDomain.HUMAN_INTERFACE),
                    ExoCapability("device.provision_dry_run", CapabilityDomain.PROVISIONING_RECOVERY),
                    ExoCapability("media.play", CapabilityDomain.MEDIA_PRESENCE),
                    ExoCapability("media.handoff", CapabilityDomain.MEDIA_PRESENCE),
                ),
                transport_ids=("home_edge_exec_gateway",),
            ),
            ExoDevice(
                identity=StableDeviceIdentity(
                    device_id="display.samsung_tv",
                    adapter_kind="samsung_adb",
                    vendor="Samsung",
                    model="Android display endpoint",
                    location="living_room",
                    registry_aliases=("samsung_tv", "samsung"),
                ),
                capabilities=(
                    ExoCapability("adb.watchdog", CapabilityDomain.PROVISIONING_RECOVERY),
                    ExoCapability("media.play", CapabilityDomain.MEDIA_PRESENCE),
                    ExoCapability("media.handoff", CapabilityDomain.MEDIA_PRESENCE),
                    ExoCapability("remote.key", CapabilityDomain.DEVICE_CONTROL, RiskLevel.YELLOW),
                ),
                transport_ids=("samsung_usb_adb_watchdog",),
            ),
            ExoDevice(
                identity=StableDeviceIdentity(
                    device_id="display.sharp_tv_living_room",
                    adapter_kind="sharp_ir",
                    vendor="Sharp",
                    model="IR television",
                    location="living_room",
                    registry_aliases=("sharp_tv_living_room", "sharp_tv"),
                ),
                capabilities=(
                    ExoCapability("ir.transmit", CapabilityDomain.DEVICE_CONTROL, RiskLevel.YELLOW),
                    ExoCapability("remote.key", CapabilityDomain.DEVICE_CONTROL, RiskLevel.YELLOW),
                ),
                transport_ids=("android_consumer_ir",),
            ),
        )
    )


def canonical_exo_adapters() -> dict[str, DeviceAdapter]:
    return {
        "home_edge": HomeEdgeDeviceAdapter(),
        "samsung_adb": SamsungDeviceAdapter(),
        "sharp_ir": SharpIrDeviceAdapter(),
    }


def canonical_exo_supervisors(
    transports: Mapping[str, Sequence[DeviceTransport]] | None = None,
    *,
    registry: ExoDeviceRegistry | None = None,
) -> dict[str, PerDeviceTransportSupervisor]:
    configured = dict(transports or {})
    source = registry or canonical_exo_registry()
    supervisors: dict[str, PerDeviceTransportSupervisor] = {}
    for device in source.devices:
        fallback = tuple(
            StaticTransport(
                transport_id,
                reason=f"{device.adapter_kind}_transport_available",
            )
            for transport_id in device.transport_ids
        )
        selected = tuple(configured.get(device.device_id, fallback))
        if not selected:
            continue
        supervisors[device.device_id] = PerDeviceTransportSupervisor(
            device_id=device.device_id,
            transports=selected,
            recovery_policy=BoundedRecoveryPolicy(max_attempts=2, allow_failover=True),
        )
    return supervisors


def canonical_exo_control_plane(
    *,
    registry: ExoDeviceRegistry | None = None,
    transports: Mapping[str, Sequence[DeviceTransport]] | None = None,
) -> ExoDeviceControlPlane:
    source = registry or canonical_exo_registry()
    return ExoDeviceControlPlane(
        registry=source,
        adapters=canonical_exo_adapters(),
        supervisors=canonical_exo_supervisors(transports, registry=source),
    )


def canonical_exo_public_registry() -> dict[str, Any]:
    return canonical_exo_registry().to_mapping()


def _blocked_result(
    command: ExoCommand,
    device: ExoDevice,
    reason: str,
    state: VerificationState,
) -> ControlResult:
    observation = DeviceObservation(
        command_id=command.command_id,
        device_id=device.device_id,
        adapter_kind=device.adapter_kind,
        capability=command.capability,
        state=state,
        reason=reason,
        transport_id=None,
    )
    return ControlResult(
        state=state,
        command_id=command.command_id,
        device_id=device.device_id,
        capability=command.capability,
        adapter_kind=device.adapter_kind,
        response={},
        observation=observation,
    )


def _observation_from_result(
    request: TransportRequest,
    result: TransportResult,
    *,
    transport_id: str | None,
    attempts: int,
    failover_count: int,
    recovery_exhausted: bool = False,
) -> DeviceObservation:
    return DeviceObservation(
        command_id=request.command_id,
        device_id=request.device_id,
        adapter_kind=request.adapter_kind,
        capability=request.capability,
        state=result.state,
        reason=result.reason,
        transport_id=transport_id,
        attempts=attempts,
        failover_count=failover_count,
        recovery_exhausted=recovery_exhausted,
        details=result.payload,
    )


def _verification_state(value: object) -> VerificationState:
    text = str(value).lower()
    if text in {"done", "ok", "success"}:
        return VerificationState.VERIFIED
    try:
        return VerificationState(text)
    except ValueError as exc:
        raise ExoControlError("verification_state_unknown") from exc


def _safe_id(value: str) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 80
        and all(ch.isascii() and (ch.isalnum() or ch in {"_", "-", "."}) for ch in value)
    )
