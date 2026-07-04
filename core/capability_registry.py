from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ExecutorCapability:
    capability_id: str
    status: str


class CapabilityRegistry:
    def __init__(self, capabilities: Mapping[str, ExecutorCapability]) -> None:
        self._capabilities = dict(capabilities)

    def resolve(self, capability_id: str) -> ExecutorCapability:
        return self._capabilities[capability_id]
