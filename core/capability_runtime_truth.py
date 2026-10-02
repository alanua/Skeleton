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

    def fresh_verified(self, now: datetime | None = None) -> bool:
        return self.fresh and self.verified and not self.time_stale(now)

    def time_stale(self, now: datetime | None = None) -> bool:
        checked_at = _coerce_now(now)
        if (
            self.expires_at is not None
            and _parse_utc_timestamp(self.expires_at, "expires_at") <= checked_at
        ):
            return True
        if (
            self.observed_at is not None
            and _parse_utc_timestamp(self.observed_at, "observed_at") > checked_at
        ):
            return True
        return False

    def to_dict(self, now: datetime | None = None) -> dict[str, str | bool]:
        payload: dict[str, str | bool] = {
            "schema": self.schema,
            "capability_id": self.capability_id,
            "runtime_state": self.runtime_state,
            "source": self.source,
            "evidence_ref": self.evidence_ref,
            "fresh": self.fresh,
            "verified": self.verified,
            "time_fresh": not self.time_stale(now),
            "fresh_verified": self.fresh_verified(now),
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

    records = []
    checked_at = _coerce_now(now)
    for capability_id in sorted(capabilities):
        capability = capabilities[capability_id]
        if not isinstance(capability, Mapping):
            raise ValueError(f"capability {capability_id} must be a mapping")

        status = capability.get("status")
        capability_evidence = tuple(evidence_by_capability.get(capability_id, ()))
        runtime_states = sorted({item.runtime_state for item in capability_evidence})
        registry_state = _registry_effective_state(status)
        fresh_verified_evidence = tuple(
            item for item in capability_evidence if item.fresh_verified(checked_at)
        )
        effective_state = _effective_state(fresh_verified_evidence)
        freshness = _freshness(capability_evidence, fresh_verified_evidence)
        drift = _drift(registry_state, effective_state, freshness)
        parity = _parity(freshness, drift)
        source_presence = _source_presence(capability, source_root)
        interface_usable = _interface_usable(
            capability,
            effective_state=effective_state,
            freshness=freshness,
            source_presence=source_presence,
        )
        live_runtime_execution = _live_runtime_execution(effective_state, freshness)
        evidence_binding = (
            "all_runtime_evidence_bound"
            if capability_evidence
            else "no_runtime_evidence"
        )
        reason_codes = _reason_codes(
            status=status,
            registry_state=registry_state,
            effective_state=effective_state,
            capability=capability,
            evidence=capability_evidence,
            fresh_verified_evidence=fresh_verified_evidence,
            source_presence=source_presence,
            interface_usable=interface_usable,
            evidence_binding=evidence_binding,
            now=checked_at,
        )
        records.append(
            {
                "capability_id": capability_id,
                "registry_status": status,
                "registry_effective_state": registry_state,
                "registry_lifecycle": _registry_lifecycle(status),
                "registry_module": capability.get("module"),
                "registry_tested": bool(capability.get("tested", False)),
                "live_runtime_execution": live_runtime_execution,
                "runtime_evidence": [
                    item.to_dict(checked_at) for item in capability_evidence
                ],
                "runtime_states": runtime_states,
                "fresh_verified_runtime_states": sorted(
                    {item.runtime_state for item in fresh_verified_evidence}
                ),
                "effective_state": effective_state,
                "freshness": freshness,
                "drift": drift,
                "parity": parity,
                "source_presence": source_presence,
                "interface_usable": interface_usable,
                "evidence_binding": evidence_binding,
                "reason_codes": reason_codes,
            }
        )

    available = [item for item in records if item["registry_status"] == "available"]
    evidence_count = sum(len(item["runtime_evidence"]) for item in records)
    fresh_verified_count = sum(
        len(
            [
                evidence
                for evidence in item["runtime_evidence"]
                if evidence["fresh_verified"]
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
            "source_present_count": len(
                [item for item in records if item["source_presence"] == "present"]
            ),
            "source_missing_count": len(
                [item for item in records if item["source_presence"] == "missing"]
            ),
            "interface_usable_count": len(
                [item for item in records if item["interface_usable"]]
            ),
        },
        "capabilities": records,
    }


def _registry_effective_state(registry_status: Any) -> str:
    return (
        "available"
        if _normalized_status(registry_status) == REGISTRY_AVAILABLE_STATUS
        else "unavailable"
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


def _effective_state(
    fresh_verified_evidence: tuple[RuntimeCapabilityEvidence, ...],
) -> str:
    if not fresh_verified_evidence:
        return "unavailable"
    return max(
        (item.runtime_state for item in fresh_verified_evidence),
        key=lambda state: RUNTIME_STATE_SEVERITY[state],
    )


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


def _source_presence(capability: Mapping[str, Any], source_root: str | Path | None) -> str:
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
    effective_state: str,
    freshness: str,
    source_presence: str,
) -> bool:
    if freshness != "fresh_verified_runtime_evidence":
        return False
    if effective_state != "available":
        return False
    if not bool(capability.get("tested", False)):
        return False
    return source_presence in {"present", "not_checked"}


def _live_runtime_execution(effective_state: str, freshness: str) -> bool:
    return (
        freshness == "fresh_verified_runtime_evidence"
        and effective_state == "available"
    )


def _reason_codes(
    *,
    status: Any,
    registry_state: str,
    effective_state: str,
    capability: Mapping[str, Any],
    evidence: tuple[RuntimeCapabilityEvidence, ...],
    fresh_verified_evidence: tuple[RuntimeCapabilityEvidence, ...],
    source_presence: str,
    interface_usable: bool,
    evidence_binding: str,
    now: datetime,
) -> list[str]:
    codes: list[str] = []
    normalized_status = _normalized_status(status)
    if normalized_status == LEGACY_STATUS:
        codes.append("LEGACY_CAPABILITY")
    if normalized_status == SUPERSEDED_STATUS:
        codes.append("SUPERSEDED_CAPABILITY")
    if registry_state != "available":
        codes.append("REGISTRY_EFFECTIVE_UNAVAILABLE")
    if not bool(capability.get("tested", False)):
        codes.append("REGISTRY_TEST_EVIDENCE_MISSING")
    if source_presence == "present":
        codes.append("REGISTRY_SOURCE_PRESENT")
    elif source_presence == "missing":
        codes.append("REGISTRY_SOURCE_MISSING")
    else:
        codes.append("REGISTRY_SOURCE_NOT_CHECKED")

    if not evidence:
        codes.append("NO_RUNTIME_EVIDENCE")
    elif evidence_binding == "all_runtime_evidence_bound":
        codes.append("RUNTIME_EVIDENCE_BOUND")
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
    if fresh_verified_evidence:
        codes.append("FRESH_VERIFIED_RUNTIME_EVIDENCE")
        if registry_state == effective_state:
            codes.append("RUNTIME_STATE_MATCHES_REGISTRY")
        elif effective_state == "available":
            codes.append("RUNTIME_OVERRIDES_STALE_REGISTRY")
        else:
            codes.append("RUNTIME_CONFLICTS_WITH_REGISTRY")
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
