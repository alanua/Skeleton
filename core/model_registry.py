from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Mapping


CAPABILITY_STATUSES = frozenset({"LIVE", "DEGRADED", "COOLDOWN", "DISABLED", "UNSUPPORTED"})
PROMOTION_STAGES = frozenset(
    {"DISCOVERED", "CANARY_ONLY", "ELIGIBLE", "LIVE", "DEGRADED", "UNSUPPORTED", "BLOCKED"}
)
HEALTH_STATUSES = frozenset({"LIVE", "DEGRADED", "COOLDOWN", "DISABLED"})
LOCALITIES = frozenset({"LOCAL", "CLOUD", "HYBRID"})


class ModelRegistryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CapabilityRecord:
    capability_id: str
    status: str
    score: float
    canary_passed: bool
    hard_failures: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    promotion_stage: str = ""

    def __post_init__(self) -> None:
        if not self.capability_id:
            raise ModelRegistryError("capability_id_required")
        if self.status not in CAPABILITY_STATUSES:
            raise ModelRegistryError("invalid_capability_status")
        if not 0.0 <= float(self.score) <= 1.0:
            raise ModelRegistryError("capability_score_out_of_range")

        stage = self.promotion_stage
        if not stage:
            stage = "ELIGIBLE" if self.status == "LIVE" and self.canary_passed and not self.hard_failures else "DISCOVERED"
            object.__setattr__(self, "promotion_stage", stage)
        if stage not in PROMOTION_STAGES:
            raise ModelRegistryError("invalid_promotion_stage")

        if self.status == "LIVE" and not self.canary_passed:
            raise ModelRegistryError("live_capability_requires_skeleton_canary")
        if self.status == "LIVE" and self.hard_failures:
            raise ModelRegistryError("live_capability_cannot_have_hard_failure")
        if stage in {"ELIGIBLE", "LIVE"} and (
            self.status != "LIVE" or not self.canary_passed or self.hard_failures
        ):
            raise ModelRegistryError("promoted_capability_requires_clean_skeleton_canary")
        if stage in {"DISCOVERED", "CANARY_ONLY"} and self.canary_passed:
            raise ModelRegistryError("preeligible_stage_cannot_have_passed_canary")
        if stage == "LIVE" and self.status != "LIVE":
            raise ModelRegistryError("live_promotion_requires_live_status")

    def eligible(self, minimum_score: float) -> bool:
        """Eligible for task-fit evaluation, not necessarily production routing."""
        return (
            self.promotion_stage in {"ELIGIBLE", "LIVE"}
            and self.status == "LIVE"
            and self.canary_passed
            and not self.hard_failures
            and self.score >= minimum_score
        )

    def production_eligible(self, minimum_score: float) -> bool:
        """Production routing requires explicit LIVE promotion in addition to evidence."""
        return self.promotion_stage == "LIVE" and self.eligible(minimum_score)

    def promote_live(self) -> "CapabilityRecord":
        if self.promotion_stage != "ELIGIBLE" or not self.eligible(0.0):
            raise ModelRegistryError("capability_not_eligible_for_live_promotion")
        return CapabilityRecord(
            capability_id=self.capability_id,
            status=self.status,
            score=self.score,
            canary_passed=self.canary_passed,
            hard_failures=self.hard_failures,
            evidence_ids=self.evidence_ids,
            promotion_stage="LIVE",
        )


@dataclass(frozen=True, slots=True)
class ModelRecord:
    model_id: str
    provider_family: str
    locality: str
    policy_approved: bool
    health: str
    privacy_classes: tuple[str, ...]
    latency_rank: int
    cost_rank: int
    capabilities: Mapping[str, CapabilityRecord]

    def __post_init__(self) -> None:
        if not self.model_id or not self.provider_family:
            raise ModelRegistryError("model_identity_required")
        if self.locality not in LOCALITIES:
            raise ModelRegistryError("invalid_model_locality")
        if self.health not in HEALTH_STATUSES:
            raise ModelRegistryError("invalid_model_health")
        if self.latency_rank < 0 or self.cost_rank < 0:
            raise ModelRegistryError("negative_rank")
        if not self.privacy_classes:
            raise ModelRegistryError("privacy_classes_required")
        if not self.policy_approved and any(
            capability.promotion_stage in {"ELIGIBLE", "LIVE"}
            for capability in self.capabilities.values()
        ):
            raise ModelRegistryError("unapproved_model_cannot_be_promoted")

    def capability(self, capability_id: str) -> CapabilityRecord | None:
        return self.capabilities.get(capability_id)


def _capability_from_mapping(capability_id: str, raw: Mapping[str, object]) -> CapabilityRecord:
    return CapabilityRecord(
        capability_id=capability_id,
        status=str(raw.get("status", "UNSUPPORTED")),
        score=float(raw.get("score", 0.0)),
        canary_passed=bool(raw.get("canary_passed", False)),
        hard_failures=tuple(str(item) for item in raw.get("hard_failures", ())),
        evidence_ids=tuple(str(item) for item in raw.get("evidence_ids", ())),
        promotion_stage=str(raw.get("promotion_stage", "")),
    )


def model_record_from_mapping(raw: Mapping[str, object]) -> ModelRecord:
    capabilities_raw = raw.get("capabilities")
    if not isinstance(capabilities_raw, Mapping):
        raise ModelRegistryError("capabilities_mapping_required")
    capabilities = {
        str(capability_id): _capability_from_mapping(str(capability_id), capability_raw)
        for capability_id, capability_raw in capabilities_raw.items()
        if isinstance(capability_raw, Mapping)
    }
    if len(capabilities) != len(capabilities_raw):
        raise ModelRegistryError("invalid_capability_record")
    return ModelRecord(
        model_id=str(raw.get("model_id", "")),
        provider_family=str(raw.get("provider_family", "")),
        locality=str(raw.get("locality", "")),
        policy_approved=bool(raw.get("policy_approved", False)),
        health=str(raw.get("health", "DISABLED")),
        privacy_classes=tuple(str(item) for item in raw.get("privacy_classes", ())),
        latency_rank=int(raw.get("latency_rank", 999)),
        cost_rank=int(raw.get("cost_rank", 999)),
        capabilities=capabilities,
    )


def registry_from_mapping(raw: Mapping[str, object]) -> tuple[ModelRecord, ...]:
    models_raw = raw.get("models")
    if not isinstance(models_raw, list):
        raise ModelRegistryError("models_list_required")
    records = tuple(model_record_from_mapping(item) for item in models_raw if isinstance(item, Mapping))
    if len(records) != len(models_raw):
        raise ModelRegistryError("invalid_model_record")
    ids = [record.model_id for record in records]
    if len(ids) != len(set(ids)):
        raise ModelRegistryError("duplicate_model_id")
    return records


def load_model_registry(path: str | Path) -> tuple[ModelRecord, ...]:
    """Load the registry from the JSON subset of YAML used by MODEL_REGISTRY.yaml."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelRegistryError("registry_parse_failed") from exc
    if not isinstance(raw, Mapping):
        raise ModelRegistryError("registry_mapping_required")
    return registry_from_mapping(raw)


def registry_snapshot_hash(records: tuple[ModelRecord, ...]) -> str:
    public = [
        {
            "model_id": record.model_id,
            "provider_family": record.provider_family,
            "locality": record.locality,
            "policy_approved": record.policy_approved,
            "health": record.health,
            "privacy_classes": list(record.privacy_classes),
            "latency_rank": record.latency_rank,
            "cost_rank": record.cost_rank,
            "capabilities": {
                key: {
                    "status": value.status,
                    "score": value.score,
                    "canary_passed": value.canary_passed,
                    "hard_failures": list(value.hard_failures),
                    "evidence_ids": list(value.evidence_ids),
                    "promotion_stage": value.promotion_stage,
                }
                for key, value in sorted(record.capabilities.items())
            },
        }
        for record in sorted(records, key=lambda item: item.model_id)
    ]
    encoded = json.dumps(public, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
