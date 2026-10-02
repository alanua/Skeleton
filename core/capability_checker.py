from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Union

import yaml

from core.capability_runtime_truth import (
    RuntimeCapabilityEvidence,
    reconcile_capability_runtime_truth,
)


class CapabilityChecker:
    def __init__(self, registry_path: Union[str, Path]) -> None:
        self.registry_path = Path(registry_path)

    def load(self) -> dict[str, Any]:
        return yaml.safe_load(self.registry_path.read_text(encoding="utf-8"))

    def available(self) -> list[str]:
        capabilities = self.load()["capabilities"]
        return [
            capability_id
            for capability_id, capability in capabilities.items()
            if capability.get("status") == "available"
        ]

    def planned(self) -> list[str]:
        capabilities = self.load()["capabilities"]
        return [
            capability_id
            for capability_id, capability in capabilities.items()
            if capability.get("status") == "planned"
        ]

    def is_available(self, capability_id: str) -> bool:
        capabilities = self.load().get("capabilities", {})
        capability = capabilities.get(capability_id)
        return bool(capability and capability.get("status") == "available")

    def runtime_truth(
        self,
        evidence: Iterable[RuntimeCapabilityEvidence] = (),
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        return reconcile_capability_runtime_truth(
            self.load(),
            evidence,
            source_root=self.registry_path.parent,
            now=now,
        )
