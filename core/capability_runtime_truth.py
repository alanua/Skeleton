from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


SCHEMA = "skeleton.capability_runtime_truth.v1"
EVIDENCE_SCHEMA = "skeleton.capability_runtime_evidence.v1"
REGISTRY_AUTHORITY = "CAPABILITY_REGISTRY.yaml"
READ_MODEL = "bounded_typed_evidence_only"
RUNTIME_STATES = frozenset(("available", "degraded", "unavailable"))
FRESHNESS_STATES = frozenset(
    (
        "no_runtime_evidence",
        "fresh_verified_runtime_evidence",
        "stale_or_unverified_runtime_evidence",
    )
)
DRIFT_STATES = frozenset(
    (
        "not_evaluated_without_fresh_evidence",
        "no_drift",
        "runtime_overrides_stale_registry",
        "runtime_conflicts_with_registry",
    )
)
PARITY_STATES = frozenset(
    ("registry_only", "registry_runtime_parity", "registry_runtime_drift")
)


@dataclass(frozen=True)
class RuntimeCapabilityEvidence:
    capability_id: str
    runtime_state: str
    source: str
    evidence_ref: str
    fresh: bool = True
    verified: bool = True
    schema: str = EVIDENCE_SCHEMA

    def __post_init__(self) -> None:
        for field_name in ("capability_id", "runtime_state", "source", "evidence_ref", "schema"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if self.schema != EVIDENCE_SCHEMA:
            raise ValueError("schema must be skeleton.capability_runtime_evidence.v1")
        if self.runtime_state not in RUNTIME_STATES:
            raise ValueError("runtime_state must be available, degraded, or unavailable")
        if not isinstance(self.fresh, bool):
            raise ValueError("fresh must be a boolean")
        if not isinstance(self.verified, bool):
            raise ValueError("verified must be a boolean")

    @property
    def fresh_verified(self) -> bool:
        return self.fresh and self.verified

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "schema": self.schema,
            "capability_id": self.capability_id,
            "runtime_state": self.runtime_state,
            "source": self.source,
            "evidence_ref": self.evidence_ref,
            "fresh": self.fresh,
            "verified": self.verified,
        }


def reconcile_capability_runtime_truth(
    registry: Mapping[str, Any],
    evidence: Iterable[RuntimeCapabilityEvidence] = (),
    *,
    registry_authority: str = REGISTRY_AUTHORITY,
) -> dict[str, Any]:
    capabilities = registry.get("capabilities", {})
    if not isinstance(capabilities, Mapping):
        raise ValueError("capability registry must contain a capabilities mapping")

    evidence_by_capability: dict[str, list[RuntimeCapabilityEvidence]] = {}
    for item in evidence:
        if not isinstance(item, RuntimeCapabilityEvidence):
            raise TypeError("runtime evidence must be RuntimeCapabilityEvidence instances")
        evidence_by_capability.setdefault(item.capability_id, []).append(item)

    unknown_evidence = sorted(
        capability_id
        for capability_id in evidence_by_capability
        if capability_id not in capabilities
    )
    if unknown_evidence:
        raise ValueError(
            "runtime evidence references unknown registry capabilities: "
            + ", ".join(unknown_evidence)
        )

    records = []
    for capability_id in sorted(capabilities):
        capability = capabilities[capability_id]
        if not isinstance(capability, Mapping):
            raise ValueError(f"capability {capability_id} must be a mapping")

        status = capability.get("status")
        capability_evidence = tuple(evidence_by_capability.get(capability_id, ()))
        runtime_states = sorted({item.runtime_state for item in capability_evidence})
        registry_state = _registry_effective_state(status)
        fresh_verified_evidence = tuple(
            item for item in capability_evidence if item.fresh_verified
        )
        effective_state = _effective_state(registry_state, fresh_verified_evidence)
        freshness = _freshness(capability_evidence, fresh_verified_evidence)
        drift = _drift(registry_state, effective_state, freshness)
        parity = _parity(freshness, drift)
        records.append(
            {
                "capability_id": capability_id,
                "registry_status": status,
                "registry_effective_state": registry_state,
                "registry_module": capability.get("module"),
                "registry_tested": bool(capability.get("tested", False)),
                "live_runtime_execution": bool(
                    capability.get("live_runtime_execution", False)
                ),
                "runtime_evidence": [item.to_dict() for item in capability_evidence],
                "runtime_states": runtime_states,
                "fresh_verified_runtime_states": sorted(
                    {item.runtime_state for item in fresh_verified_evidence}
                ),
                "effective_state": effective_state,
                "freshness": freshness,
                "drift": drift,
                "parity": parity,
            }
        )

    available = [item for item in records if item["registry_status"] == "available"]
    evidence_count = sum(len(item["runtime_evidence"]) for item in records)
    fresh_verified_count = sum(
        len(
            [
                evidence
                for evidence in item["runtime_evidence"]
                if evidence["fresh"] and evidence["verified"]
            ]
        )
        for item in records
    )

    return {
        "schema": SCHEMA,
        "registry_authority": registry_authority,
        "read_model": READ_MODEL,
        "runtime_probe_performed": False,
        "runtime_mutation_performed": False,
        "summary": {
            "registry_capability_count": len(records),
            "registry_available_count": len(available),
            "typed_runtime_evidence_count": evidence_count,
            "fresh_verified_runtime_evidence_count": fresh_verified_count,
            "effective_available_count": len(
                [item for item in records if item["effective_state"] == "available"]
            ),
            "drift_count": len(
                [item for item in records if item["parity"] == "registry_runtime_drift"]
            ),
            "parity_match_count": len(
                [item for item in records if item["parity"] == "registry_runtime_parity"]
            ),
        },
        "capabilities": records,
    }


def _registry_effective_state(registry_status: Any) -> str:
    return "available" if registry_status == "available" else "unavailable"


def _effective_state(
    registry_state: str, fresh_verified_evidence: tuple[RuntimeCapabilityEvidence, ...]
) -> str:
    if not fresh_verified_evidence:
        return registry_state
    states = {item.runtime_state for item in fresh_verified_evidence}
    for state in ("available", "degraded", "unavailable"):
        if state in states:
            return state
    return registry_state


def _freshness(
    evidence: tuple[RuntimeCapabilityEvidence, ...],
    fresh_verified_evidence: tuple[RuntimeCapabilityEvidence, ...],
) -> str:
    if fresh_verified_evidence:
        return "fresh_verified_runtime_evidence"
    if evidence:
        return "stale_or_unverified_runtime_evidence"
    return "no_runtime_evidence"


def _drift(registry_state: str, effective_state: str, freshness: str) -> str:
    if freshness != "fresh_verified_runtime_evidence":
        return "not_evaluated_without_fresh_evidence"
    if registry_state == effective_state:
        return "no_drift"
    if effective_state == "available":
        return "runtime_overrides_stale_registry"
    return "runtime_conflicts_with_registry"


def _parity(freshness: str, drift: str) -> str:
    if freshness == "no_runtime_evidence":
        return "registry_only"
    if drift == "no_drift":
        return "registry_runtime_parity"
    return "registry_runtime_drift"
