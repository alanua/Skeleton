from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.model_registry import (
    CapabilityRecord,
    ModelRegistryError,
    load_model_registry,
    registry_snapshot_hash,
)


def test_registry_loads_current_roster() -> None:
    records = load_model_registry(Path(__file__).resolve().parents[1] / "MODEL_REGISTRY.yaml")
    assert {record.model_id for record in records} >= {
        "local-small",
        "openrouter-glm-free-challenger",
        "openrouter-kimi-k2-challenger",
    }


def test_registry_snapshot_is_deterministic() -> None:
    path = Path(__file__).resolve().parents[1] / "MODEL_REGISTRY.yaml"
    records = load_model_registry(path)
    assert registry_snapshot_hash(records) == registry_snapshot_hash(tuple(reversed(records)))


def test_live_capability_requires_skeleton_canary() -> None:
    with pytest.raises(ModelRegistryError, match="live_capability_requires_skeleton_canary"):
        CapabilityRecord("reasoning", "LIVE", 0.99, False)


def test_hard_failure_cannot_be_hidden_inside_live_status() -> None:
    with pytest.raises(ModelRegistryError, match="live_capability_cannot_have_hard_failure"):
        CapabilityRecord("repository_edit", "LIVE", 0.9, True, ("DELIVERABLE_MISSING",))


def test_registry_file_is_json_yaml_subset() -> None:
    raw = json.loads((Path(__file__).resolve().parents[1] / "MODEL_REGISTRY.yaml").read_text())
    assert raw["schema"] == "skeleton.model_registry.v1"
