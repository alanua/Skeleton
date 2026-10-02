from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


SCHEMA = "skeleton.capability_runtime_truth.v1"
EVIDENCE_SCHEMA = "skeleton.capability_runtime_evidence.v1"
REGISTRY_AUTHORITY = "CAPABILITY_REGISTRY.yaml"
READ_MODEL = "bounded_typed_evidence_only"
RUNTIME_STATES = frozenset(("available", "degraded", "unavailable"))


@dataclass(frozen=True)
class RuntimeCapabilityEvidence:
    capability_id: str
    runtime_state: str
    source: str
    evidence_ref: str
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

    def to_dict(self) -> dict[str, str]:
        return {
            "schema": self.schema,
            "capability_id": self.capability_id,
            "runtime_state": self.runtime_state,
            "source": self.source,
            "evidence_ref": self.evidence_ref,
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
        records.append(
            {
                "capability_id": capability_id,
                "registry_status": status,
                "registry_module": capability.get("module"),
                "registry_tested": bool(capability.get("tested", False)),
                "live_runtime_execution": bool(
                    capability.get("live_runtime_execution", False)
                ),
                "runtime_evidence": [item.to_dict() for item in capability_evidence],
                "runtime_states": runtime_states,
                "truth_status": _truth_status(str(status), runtime_states),
            }
        )

    available = [item for item in records if item["registry_status"] == "available"]
    evidence_count = sum(len(item["runtime_evidence"]) for item in records)
    supported = [
        item
        for item in available
        if "available" in item["runtime_states"] or "degraded" in item["runtime_states"]
    ]

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
            "registry_available_with_runtime_evidence_count": len(supported),
        },
        "capabilities": records,
    }


def _truth_status(registry_status: str, runtime_states: list[str]) -> str:
    if not runtime_states:
        return "registry_authoritative_no_runtime_evidence"
    if registry_status != "available":
        return "registry_authoritative_runtime_evidence_ignored"
    if "available" in runtime_states:
        return "registry_available_runtime_confirmed"
    if "degraded" in runtime_states:
        return "registry_available_runtime_degraded"
    return "registry_available_runtime_unavailable"
