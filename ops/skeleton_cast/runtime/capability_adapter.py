from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RuntimeResolverCapability:
    capability_id: str
    version: str
    supported_hosts: tuple[str, ...]
    runtime_entrypoint: str
    deploy_operation: str
    verify_operation: str
    rollback_operation: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": "skeleton.resolver_capability.runtime_binding.v1",
            "capability_id": self.capability_id,
            "version": self.version,
            "supported_hosts": list(self.supported_hosts),
            "runtime_entrypoint": self.runtime_entrypoint,
            "deploy_operation": self.deploy_operation,
            "verify_operation": self.verify_operation,
            "rollback_operation": self.rollback_operation,
        }


ANITUBE_ORIGIN_PROTECTED_CAPABILITY = RuntimeResolverCapability(
    capability_id="skeleton_cast.anitube.origin_protected",
    version="1.0.0",
    supported_hosts=("anitube.in.ua", "www.anitube.in.ua"),
    runtime_entrypoint="ops.skeleton_cast.runtime.resolver.discover",
    deploy_operation="skeleton_cast.resolver.deploy",
    verify_operation="skeleton_cast.resolver.verify",
    rollback_operation="skeleton_cast.resolver.rollback",
)


def active_resolver_capabilities() -> tuple[RuntimeResolverCapability, ...]:
    return (ANITUBE_ORIGIN_PROTECTED_CAPABILITY,)
