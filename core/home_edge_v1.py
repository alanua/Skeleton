from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Protocol
from uuid import uuid4


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


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    NEED_APPROVAL = "need_approval"
    DENY = "deny"


@dataclass(frozen=True)
class Capability:
    name: str
    domain: CapabilityDomain
    risk: RiskLevel = RiskLevel.GREEN


@dataclass(frozen=True)
class RegistryNode:
    node_id: str
    node_type: str
    current_host: str | None = None
    capabilities: tuple[Capability, ...] = ()
    portable_role: bool = False

    def capability(self, capability_name: str) -> Capability | None:
        return next((item for item in self.capabilities if item.name == capability_name), None)


@dataclass(frozen=True)
class RegistryDevice:
    device_id: str
    device_type: str
    location: str | None = None
    capabilities: tuple[Capability, ...] = ()


@dataclass(frozen=True)
class RegistryService:
    service_id: str
    service_type: str
    owner_node_id: str
    capabilities: tuple[Capability, ...] = ()


@dataclass(frozen=True)
class DeviceRegistry:
    nodes: tuple[RegistryNode, ...] = ()
    devices: tuple[RegistryDevice, ...] = ()
    services: tuple[RegistryService, ...] = ()

    def resolve(self, capability_name: str) -> tuple[RegistryNode, Capability] | None:
        for node in self.nodes:
            capability = node.capability(capability_name)
            if capability is not None:
                return node, capability
        return None


@dataclass(frozen=True)
class HomeEdgeTask:
    task_type: str
    capability: str
    target: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    risk: RiskLevel = RiskLevel.GREEN
    task_id: str = field(default_factory=lambda: f"home-edge-task-{uuid4()}")
    dry_run: bool = True


@dataclass(frozen=True)
class AuditEvent:
    task_id: str
    target: str
    capability: str
    domain: CapabilityDomain | None
    decision: PolicyDecision
    status: str
    message: str


class HomeEdgePolicy:
    def decide(self, task: HomeEdgeTask) -> PolicyDecision:
        if not task.dry_run:
            return PolicyDecision.NEED_APPROVAL
        if task.risk == RiskLevel.GREEN:
            return PolicyDecision.ALLOW
        return PolicyDecision.NEED_APPROVAL


class HomeEdgeExecutor(Protocol):
    domain: CapabilityDomain

    def run(self, task: HomeEdgeTask, node: RegistryNode) -> dict[str, Any]: ...


@dataclass(frozen=True)
class DomainDryRunExecutor:
    domain: CapabilityDomain

    def run(self, task: HomeEdgeTask, node: RegistryNode) -> dict[str, Any]:
        return {
            "status": "dry_run",
            "task_id": task.task_id,
            "node_id": node.node_id,
            "domain": self.domain.value,
            "capability": task.capability,
            "target": task.target,
        }


class ConnectivityExecutor(DomainDryRunExecutor):
    def __init__(self) -> None:
        super().__init__(CapabilityDomain.CONNECTIVITY)


class DeviceControlExecutor(DomainDryRunExecutor):
    def __init__(self) -> None:
        super().__init__(CapabilityDomain.DEVICE_CONTROL)


class NetworkAdminExecutor(DomainDryRunExecutor):
    def __init__(self) -> None:
        super().__init__(CapabilityDomain.NETWORK_ADMIN)


class ServiceHubExecutor(DomainDryRunExecutor):
    def __init__(self) -> None:
        super().__init__(CapabilityDomain.SERVICE_HUB)


class HumanInterfaceExecutor(DomainDryRunExecutor):
    def __init__(self) -> None:
        super().__init__(CapabilityDomain.HUMAN_INTERFACE)


class ProvisioningRecoveryExecutor(DomainDryRunExecutor):
    def __init__(self) -> None:
        super().__init__(CapabilityDomain.PROVISIONING_RECOVERY)


class MediaPresenceExecutor(DomainDryRunExecutor):
    def __init__(self) -> None:
        super().__init__(CapabilityDomain.MEDIA_PRESENCE)


def default_executors() -> dict[CapabilityDomain, HomeEdgeExecutor]:
    executors: tuple[HomeEdgeExecutor, ...] = (
        ConnectivityExecutor(),
        DeviceControlExecutor(),
        NetworkAdminExecutor(),
        ServiceHubExecutor(),
        HumanInterfaceExecutor(),
        ProvisioningRecoveryExecutor(),
        MediaPresenceExecutor(),
    )
    return {executor.domain: executor for executor in executors}


class HomeEdgeRouter:
    def __init__(
        self,
        registry: DeviceRegistry,
        policy: HomeEdgePolicy | None = None,
        executors: Mapping[CapabilityDomain, HomeEdgeExecutor] | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy or HomeEdgePolicy()
        self.executors = dict(executors or default_executors())

    def route(self, task: HomeEdgeTask) -> tuple[dict[str, Any] | None, AuditEvent]:
        decision = self.policy.decide(task)
        if decision != PolicyDecision.ALLOW:
            return None, AuditEvent(
                task_id=task.task_id,
                target=task.target,
                capability=task.capability,
                domain=None,
                decision=decision,
                status="blocked",
                message="task requires approval before execution",
            )

        resolved = self.registry.resolve(task.capability)
        if resolved is None:
            return None, AuditEvent(
                task_id=task.task_id,
                target=task.target,
                capability=task.capability,
                domain=None,
                decision=decision,
                status="blocked",
                message="no node with requested capability",
            )

        node, capability = resolved
        executor = self.executors.get(capability.domain)
        if executor is None:
            return None, AuditEvent(
                task_id=task.task_id,
                target=task.target,
                capability=task.capability,
                domain=capability.domain,
                decision=PolicyDecision.DENY,
                status="blocked",
                message="no executor for capability domain",
            )

        result = executor.run(task, node)
        return result, AuditEvent(
            task_id=task.task_id,
            target=task.target,
            capability=task.capability,
            domain=capability.domain,
            decision=decision,
            status="ok",
            message="dry-run routed through capability registry",
        )


def home_edge_v1_bootstrap_registry() -> DeviceRegistry:
    return DeviceRegistry(
        nodes=(
            RegistryNode(
                node_id="home-edge-role",
                node_type="portable_home_gateway",
                current_host="home-edge-01",
                portable_role=True,
                capabilities=(
                    Capability("connectivity.health", CapabilityDomain.CONNECTIVITY),
                    Capability("ha.service_call", CapabilityDomain.DEVICE_CONTROL),
                    Capability("network.observe", CapabilityDomain.NETWORK_ADMIN),
                    Capability("service.file_status", CapabilityDomain.SERVICE_HUB),
                    Capability("ui.display", CapabilityDomain.HUMAN_INTERFACE),
                    Capability("device.provision_dry_run", CapabilityDomain.PROVISIONING_RECOVERY),
                    Capability("media.play", CapabilityDomain.MEDIA_PRESENCE),
                ),
            ),
        ),
        devices=(
            RegistryDevice("display-client", "human_interface", capabilities=(Capability("ui.display", CapabilityDomain.HUMAN_INTERFACE),)),
            RegistryDevice("smart-light", "home_automation", capabilities=(Capability("ha.service_call", CapabilityDomain.DEVICE_CONTROL),)),
        ),
        services=(
            RegistryService("home_assistant", "home_automation", "home-edge-role", (Capability("ha.service_call", CapabilityDomain.DEVICE_CONTROL),)),
            RegistryService("logitech_media_server", "media", "home-edge-role", (Capability("media.play", CapabilityDomain.MEDIA_PRESENCE),)),
        ),
    )
