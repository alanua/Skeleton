from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA = "skeleton.capability_runtime_truth.v1"
EVIDENCE_SCHEMA = "skeleton.capability_runtime_evidence.v1"
REGISTRY_AUTHORITY = "CAPABILITY_REGISTRY.yaml"
READ_MODEL = "bounded_typed_evidence_only"

RUNTIME_STATES = frozenset(("available", "degraded", "unavailable"))
RUNTIME_STATE_SEVERITY = {
    "available": 0,
    "degraded": 1,
    "unavailable": 2,
}
REGISTRY_AVAILABLE_STATUS = "available"
LEGACY_STATUS = "legacy"
SUPERSEDED_STATUS = "superseded"

REGISTRY_DECLARED_STATES = frozenset(("declared_available", "declared_unavailable"))
EVIDENCE_STATES = frozenset(
    (
        "no_runtime_evidence",
        "accepted_runtime_evidence",
        "stale_runtime_evidence",
    )
)
REGISTRY_RUNTIME_RELATIONS = frozenset(
    (
        "registry_only",
        "runtime_matches_registry",
        "runtime_overrides_registry",
        "runtime_conflicts_with_registry",
        "runtime_not_accepted",
    )
)
SOURCE_STATES = frozenset(("present", "missing", "not_checked"))


@dataclass(frozen=True)
class RuntimeCapabilityEvidence:
    capability_id: str
    runtime_state: str
    source: str
    evidence_ref: str
    fresh: bool = True
    verified: bool = True
    observed_at: str | None = None
    expires_at: str | None = None
    schema: str = EVIDENCE_SCHEMA

    def __post_init__(self) -> None:
        for field_name in (
            "capability_id",
            "runtime_state",
            "source",
            "evidence_ref",
            "schema",
        ):
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
        for field_name in ("observed_at", "expires_at"):
            value = getattr(self, field_name)
            if value is not None:
                _parse_utc_timestamp(value, field_name)

    def time_current(self, now: datetime | None = None) -> bool:
        checked_at = _coerce_now(now)
        if (
            self.expires_at is not None
            and _parse_utc_timestamp(self.expires_at, "expires_at") <= checked_at
        ):
            return False
        if (
            self.observed_at is not None
            and _parse_utc_timestamp(self.observed_at, "observed_at") > checked_at
        ):
            return False
        return True

    def accepted_for_truth(self, now: datetime | None = None) -> bool:
        return self.fresh and self.verified and self.time_current(now)

    def to_dict(self, now: datetime | None = None) -> dict[str, str | bool]:
        payload: dict[str, str | bool] = {
            "schema": self.schema,
            "capability_id": self.capability_id,
            "runtime_state": self.runtime_state,
            "evidence_source": self.source,
            "evidence_ref": self.evidence_ref,
            "fresh": self.fresh,
            "verified": self.verified,
            "time_current": self.time_current(now),
            "accepted_for_truth": self.accepted_for_truth(now),
        }
        if self.observed_at is not None:
            payload["observed_at"] = self.observed_at
        if self.expires_at is not None:
            payload["expires_at"] = self.expires_at
        return payload


def reconcile_capability_runtime_truth(
    registry: Mapping[str, Any],
    evidence: Iterable[RuntimeCapabilityEvidence] = (),
    *,
    registry_authority: str = REGISTRY_AUTHORITY,
    source_root: str | Path | None = None,
    now: datetime | str | None = None,
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

    checked_at = _coerce_now(now)
    records = []
    for capability_id in sorted(capabilities):
        capability = capabilities[capability_id]
        if not isinstance(capability, Mapping):
            raise ValueError(f"capability {capability_id} must be a mapping")

        status = capability.get("status")
        capability_evidence = tuple(evidence_by_capability.get(capability_id, ()))
        accepted_evidence = tuple(
            item for item in capability_evidence if item.accepted_for_truth(checked_at)
        )
        runtime_states = sorted({item.runtime_state for item in capability_evidence})
        accepted_states = sorted({item.runtime_state for item in accepted_evidence})
        declared_state = _registry_declared_state(status)
        derived_state = _derived_runtime_state(accepted_evidence)
        evidence_state = _runtime_evidence_state(capability_evidence, accepted_evidence)
        relation = _registry_runtime_relation(
            declared_state=declared_state,
            derived_state=derived_state,
            evidence_state=evidence_state,
        )
        source_state = _source_state(capability, source_root)
        interface_usable = _interface_usable(
            capability,
            derived_state=derived_state,
            evidence_state=evidence_state,
            source_state=source_state,
        )
        reason_codes = _reason_codes(
            status=status,
            declared_state=declared_state,
            derived_state=derived_state,
            capability=capability,
            evidence=capability_evidence,
            accepted_evidence=accepted_evidence,
            source_state=source_state,
            interface_usable=interface_usable,
            relation=relation,
            now=checked_at,
        )
        records.append(
            {
                "capability_id": capability_id,
                "registry_status": status,
                "registry_lifecycle": _registry_lifecycle(status),
                "registry_declared_state": declared_state,
                "registry_module": capability.get("module"),
                "registry_tested": bool(capability.get("tested", False)),
                "runtime_evidence": [
                    item.to_dict(checked_at) for item in capability_evidence
                ],
                "runtime_states": runtime_states,
                "accepted_runtime_states": accepted_states,
                "derived_runtime_state": derived_state,
                "runtime_evidence_state": evidence_state,
                "registry_runtime_relation": relation,
                "source_state": source_state,
                "interface_usable": interface_usable,
                "live_runtime_execution": (
                    evidence_state == "accepted_runtime_evidence"
                    and derived_state == "available"
                ),
                "reason_codes": reason_codes,
            }
        )

    available = [item for item in records if item["registry_status"] == "available"]
    evidence_count = sum(len(item["runtime_evidence"]) for item in records)
    accepted_count = sum(
        len(
            [
                evidence
                for evidence in item["runtime_evidence"]
                if evidence["accepted_for_truth"]
            ]
        )
        for item in records
    )

    return {
        "schema": SCHEMA,
        "registry_authority": registry_authority,
        "read_model": READ_MODEL,
        "checked_at": _format_utc_timestamp(checked_at),
        "runtime_probe_performed": False,
        "runtime_mutation_performed": False,
        "summary": {
            "registry_capability_count": len(records),
            "registry_available_count": len(available),
            "typed_runtime_evidence_count": evidence_count,
            "accepted_runtime_evidence_count": accepted_count,
            "derived_available_count": len(
                [item for item in records if item["derived_runtime_state"] == "available"]
            ),
            "derived_degraded_count": len(
                [item for item in records if item["derived_runtime_state"] == "degraded"]
            ),
            "derived_unavailable_count": len(
                [
                    item
                    for item in records
                    if item["derived_runtime_state"] == "unavailable"
                ]
            ),
            "registry_runtime_conflict_count": len(
                [
                    item
                    for item in records
                    if item["registry_runtime_relation"]
                    == "runtime_conflicts_with_registry"
                ]
            ),
            "runtime_override_count": len(
                [
                    item
                    for item in records
                    if item["registry_runtime_relation"] == "runtime_overrides_registry"
                ]
            ),
            "source_present_count": len(
                [item for item in records if item["source_state"] == "present"]
            ),
            "source_missing_count": len(
                [item for item in records if item["source_state"] == "missing"]
            ),
            "interface_usable_count": len(
                [item for item in records if item["interface_usable"]]
            ),
        },
        "capabilities": records,
    }


def _registry_declared_state(registry_status: Any) -> str:
    return (
        "declared_available"
        if _normalized_status(registry_status) == REGISTRY_AVAILABLE_STATUS
        else "declared_unavailable"
    )


def _registry_lifecycle(registry_status: Any) -> str:
    normalized = _normalized_status(registry_status)
    if normalized == LEGACY_STATUS:
        return "legacy"
    if normalized == SUPERSEDED_STATUS:
        return "superseded"
    return "active"


def _normalized_status(registry_status: Any) -> str:
    return registry_status.strip().lower() if isinstance(registry_status, str) else ""


def _derived_runtime_state(
    accepted_evidence: tuple[RuntimeCapabilityEvidence, ...],
) -> str:
    if not accepted_evidence:
        return "unavailable"
    return max(
        (item.runtime_state for item in accepted_evidence),
        key=lambda state: RUNTIME_STATE_SEVERITY[state],
    )


def _runtime_evidence_state(
    evidence: tuple[RuntimeCapabilityEvidence, ...],
    accepted_evidence: tuple[RuntimeCapabilityEvidence, ...],
) -> str:
    if accepted_evidence:
        return "accepted_runtime_evidence"
    if evidence:
        return "stale_runtime_evidence"
    return "no_runtime_evidence"


def _registry_runtime_relation(
    *,
    declared_state: str,
    derived_state: str,
    evidence_state: str,
) -> str:
    if evidence_state == "no_runtime_evidence":
        return "registry_only"
    if evidence_state != "accepted_runtime_evidence":
        return "runtime_not_accepted"
    if declared_state == "declared_available" and derived_state == "available":
        return "runtime_matches_registry"
    if declared_state == "declared_unavailable" and derived_state == "available":
        return "runtime_overrides_registry"
    return "runtime_conflicts_with_registry"


def _source_state(capability: Mapping[str, Any], source_root: str | Path | None) -> str:
    module = capability.get("module")
    if not isinstance(module, str) or not module.strip():
        return "missing"
    if source_root is None:
        return "not_checked"

    module_path = Path(module)
    if module_path.is_absolute() or ".." in module_path.parts:
        return "missing"
    return "present" if (Path(source_root) / module_path).exists() else "missing"


def _interface_usable(
    capability: Mapping[str, Any],
    *,
    derived_state: str,
    evidence_state: str,
    source_state: str,
) -> bool:
    if evidence_state != "accepted_runtime_evidence":
        return False
    if derived_state != "available":
        return False
    if not bool(capability.get("tested", False)):
        return False
    return source_state in {"present", "not_checked"}


def _reason_codes(
    *,
    status: Any,
    declared_state: str,
    derived_state: str,
    capability: Mapping[str, Any],
    evidence: tuple[RuntimeCapabilityEvidence, ...],
    accepted_evidence: tuple[RuntimeCapabilityEvidence, ...],
    source_state: str,
    interface_usable: bool,
    relation: str,
    now: datetime,
) -> list[str]:
    codes: list[str] = []
    normalized_status = _normalized_status(status)
    if normalized_status == LEGACY_STATUS:
        codes.append("LEGACY_CAPABILITY")
    if normalized_status == SUPERSEDED_STATUS:
        codes.append("SUPERSEDED_CAPABILITY")
    if declared_state == "declared_unavailable":
        codes.append("REGISTRY_DECLARED_UNAVAILABLE")
    if not bool(capability.get("tested", False)):
        codes.append("REGISTRY_TEST_EVIDENCE_MISSING")
    if source_state == "present":
        codes.append("REGISTRY_SOURCE_PRESENT")
    elif source_state == "missing":
        codes.append("REGISTRY_SOURCE_MISSING")
    else:
        codes.append("REGISTRY_SOURCE_NOT_CHECKED")

    if not evidence:
        codes.append("NO_RUNTIME_EVIDENCE")
    for item in evidence:
        if not item.fresh:
            codes.append("STALE_RUNTIME_EVIDENCE")
        if not item.verified:
            codes.append("UNVERIFIED_RUNTIME_EVIDENCE")
        if (
            item.expires_at is not None
            and _parse_utc_timestamp(item.expires_at, "expires_at") <= now
        ):
            codes.append("RUNTIME_EVIDENCE_EXPIRED")
        if (
            item.observed_at is not None
            and _parse_utc_timestamp(item.observed_at, "observed_at") > now
        ):
            codes.append("RUNTIME_EVIDENCE_FROM_FUTURE")
    if accepted_evidence:
        codes.append("ACCEPTED_RUNTIME_EVIDENCE")
        if relation == "runtime_matches_registry":
            codes.append("RUNTIME_MATCHES_REGISTRY")
        elif relation == "runtime_overrides_registry":
            codes.append("RUNTIME_OVERRIDES_REGISTRY")
        else:
            codes.append("RUNTIME_CONFLICTS_WITH_REGISTRY")
    elif evidence:
        codes.append("RUNTIME_EVIDENCE_NOT_ACCEPTED")
    if derived_state == "degraded":
        codes.append("RUNTIME_DERIVED_DEGRADED")
    if derived_state == "unavailable":
        codes.append("RUNTIME_DERIVED_UNAVAILABLE")
    if interface_usable:
        codes.append("INTERFACE_USABLE")
    else:
        codes.append("INTERFACE_NOT_USABLE")
    return sorted(set(codes))


def _coerce_now(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, str):
        return _parse_utc_timestamp(value, "now")
    raise TypeError("now must be a datetime, ISO timestamp string, or None")


def _parse_utc_timestamp(value: str, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty UTC timestamp")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _format_utc_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
