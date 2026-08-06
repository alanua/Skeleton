from __future__ import annotations

from pathlib import Path

import pytest

from core.video_provider_registry import (
    LocalProvider,
    VideoProviderRegistryError,
    synthetic_local_provider_registry,
)


def test_registry_routes_only_allowlisted_local_providers(tmp_path: Path) -> None:
    registry = synthetic_local_provider_registry(tmp_path)

    inventory = registry.public_inventory()

    assert inventory["local_only"] is True
    assert inventory["cloud_fallback"] is False
    assert registry.route("ocr").name == "tesseract-local"
    with pytest.raises(VideoProviderRegistryError) as exc:
        registry.require("model", "gpt-cloud")
    assert exc.value.reason_code == "PROVIDER_NOT_ALLOWLISTED"


def test_registry_rejects_cloud_or_network_provider(tmp_path: Path) -> None:
    with pytest.raises(VideoProviderRegistryError) as exc:
        LocalProvider(
            "openai-cloud",
            "model",
            tmp_path / "bin" / "model",
            ("https://api.openai.com/v1",),
            1000,
            10,
            "BAD_PROVIDER",
        )

    assert exc.value.reason_code == "CLOUD_PROVIDER_FORBIDDEN"
