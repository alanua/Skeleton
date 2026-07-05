from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping
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

    def supports(self, capability_name: str) -> bool:
        return any(capability.name == capability_name for capability in self.capabilities)


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

    def resolve_node_for_capability(self, capability_name: str) -> RegistryNode | None:
        for node in self.nodes:
            if node.supports(capability_name):
                return node
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
    decision: PolicyDecision
    status: str
    message: str


class HomeEdgePolicy:
    def decide(self, task: HomeEdgeTask) -> PolicyDecision:
        if task.risk == RiskLevel.GREEN:
            return PolicyDecision.ALLOW
        if task.risk == RiskLevel.YELLOW:
            return PolicyDecision.NEED_APPROVAL
        return PolicyDecision.NEED_APPROVAL


class DryRunExecutor:
    def run(self, task: HomeEdgeTask, node: RegistryNode) -> dict[str, Any]:
        return {
            "status": "dry_run",
            "task_id": task.task_id,
            "node_id": node.node_id,
            "capability": task.capability,
            "target": task.target,
        }


class HomeEdgeRouter:
    def __init__(
        self,
        registry: DeviceRegistry,
        policy: HomeEdgePolicy | None = None,
        executor: DryRunExecutor | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy or HomeEdgePolicy()
        self.executor = executor or DryRunExecutor()

    def route(self, task: HomeEdgeTask) -> tuple[dict[str, Any] | None, AuditEvent]:
        decision = self.policy.decide(task)
        if decision != PolicyDecision.ALLOW:
            return None, AuditEvent(
                task_id=task.task_id,
                target=task.target,
                capability=task.capability,
                decision=decision,
                status="blocked",
                message="task requires approval before execution",
            )

        node = self.registry.resolve_node_for_capability(task.capability)
        if node is None:
            return None, AuditEvent(
                task_id=task.task_id,
                target=task.target,
                capability=task.capability,
                decision=decision,
                status="blocked",
                message="no node with requested capability",
            )

        result = self.executor.run(task, node)
        return result, AuditEvent(
            task_id=task.task_id,
            target=task.target,
            capability=task.capability,
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
                    Capability("ha.service_call", CapabilityDomain.DEVICE_CONTROL),
                    Capability("media.play", CapabilityDomain.MEDIA_PRESENCE),
                    Capability("network.observe", CapabilityDomain.NETWORK_ADMIN),
                    Capability("service.file_status", CapabilityDomain.SERVICE_HUB),
                    Capability("ui.display", CapabilityDomain.HUMAN_INTERFACE),
                    Capability("device.provision_dry_run", CapabilityDomain.PROVISIONING_RECOVERY),
                    Capability("connectivity.health", CapabilityDomain.CONNECTIVITY),
                ),
            ),
        ),
        services=(
            RegistryService("home_assistant", "home_automation", "home-edge-role", (Capability("ha.service_call", CapabilityDomain.DEVICE_CONTROL),)),
            RegistryService("logitech_media_server", "media", "home-edge-role", (Capability("media.play", CapabilityDomain.MEDIA_PRESENCE),)),
        ),
    )
