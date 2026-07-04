from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from core.task_envelope import EXECUTOR_CLASSES


class CapabilityRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class ExecutorCapability:
    capability_id: str
    status: str


class CapabilityRegistry:
    def __init__(self, capabilities: Mapping[str, ExecutorCapability]) -> None:
        values = dict(capabilities)
        if set(values) - set(EXECUTOR_CLASSES):
            raise CapabilityRegistryError("unknown executor capability")
        self._capabilities = values

    def resolve(self, capability_id: str) -> ExecutorCapability:
        capability = self._capabilities.get(capability_id)
        if capability is None:
            raise CapabilityRegistryError("executor capability not registered")
        if capability.status != "available":
            raise CapabilityRegistryError("executor capability disabled")
        return capability

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._capabilities))
