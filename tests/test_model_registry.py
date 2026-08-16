from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.model_registry import (
    CapabilityRecord,
    ModelRecord,
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


def test_canary_pass_defaults_to_eligible_not_production_live() -> None:
    capability = CapabilityRecord("repository_edit", "LIVE", 0.9, True)
    assert capability.promotion_stage == "ELIGIBLE"
    assert capability.eligible(0.8)
    assert not capability.production_eligible(0.8)


def test_explicit_live_promotion_requires_eligible_clean_canary() -> None:
    capability = CapabilityRecord(
        "repository_edit", "LIVE", 0.9, True, promotion_stage="ELIGIBLE"
    )
    promoted = capability.promote_live()
    assert promoted.promotion_stage == "LIVE"
    assert promoted.production_eligible(0.8)

    discovered = CapabilityRecord(
        "repository_edit", "DEGRADED", 0.7, False, promotion_stage="DISCOVERED"
    )
    with pytest.raises(ModelRegistryError, match="capability_not_eligible_for_live_promotion"):
        discovered.promote_live()


def test_unapproved_model_cannot_hold_eligible_capability() -> None:
    with pytest.raises(ModelRegistryError, match="unapproved_model_cannot_be_promoted"):
        ModelRecord(
            model_id="unapproved",
            provider_family="test",
            locality="CLOUD",
            policy_approved=False,
            health="LIVE",
            privacy_classes=("PUBLIC",),
            latency_rank=1,
            cost_rank=1,
            capabilities={
                "reasoning": CapabilityRecord(
                    "reasoning", "LIVE", 0.9, True, promotion_stage="ELIGIBLE"
                )
            },
        )


def test_kimi_challenger_is_eligible_but_not_live_from_canary_alone() -> None:
    records = load_model_registry(Path(__file__).resolve().parents[1] / "MODEL_REGISTRY.yaml")
    kimi = next(record for record in records if record.model_id == "openrouter-kimi-k2-challenger")
    assert kimi.capability("repository_edit").promotion_stage == "ELIGIBLE"
    assert not kimi.capability("repository_edit").production_eligible(0.0)


def test_registry_file_is_json_yaml_subset() -> None:
    raw = json.loads((Path(__file__).resolve().parents[1] / "MODEL_REGISTRY.yaml").read_text())
    assert raw["schema"] == "skeleton.model_registry.v1"
