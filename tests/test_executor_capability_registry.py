from __future__ import annotations

import pytest

from core.capability_registry import (
    CapabilityRegistry,
    CapabilityRegistryError,
    ExecutorCapability,
)


def test_registry_resolves_available_executor() -> None:
    registry = CapabilityRegistry(
        {
            "network.http": ExecutorCapability(
                capability_id="network.http",
                status="available",
            )
        }
    )

    assert registry.resolve("network.http").capability_id == "network.http"
    assert registry.ids() == ("network.http",)


def test_registry_rejects_unknown_executor_class() -> None:
    with pytest.raises(CapabilityRegistryError, match="unknown"):
        CapabilityRegistry(
            {
                "device.wled": ExecutorCapability(
                    capability_id="device.wled",
                    status="available",
                )
            }
        )


def test_registry_rejects_disabled_executor() -> None:
    registry = CapabilityRegistry(
        {
            "network.http": ExecutorCapability(
                capability_id="network.http",
                status="disabled",
            )
        }
    )

    with pytest.raises(CapabilityRegistryError, match="disabled"):
        registry.resolve("network.http")
