from pathlib import Path
from typing import Any, Union

import yaml


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
