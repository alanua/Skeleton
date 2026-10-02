from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA = "skeleton.capability_runtime_truth.v1"
EVIDENCE_SCHEMA = "skeleton.capability_runtime_evidence.v1"
REGISTRY_AUTHORITY = "CAPABILITY_REGISTRY.yaml"
READ_MODEL = "bounded_typed_evidence_only"

EFFECTIVE_STATUSES = frozenset(
    ("LIVE", "PARTIAL", "CONTRACT_ONLY", "BLOCKED", "LEGACY", "SUPERSEDED")
)
FRESHNESS_VALUES = frozenset(("FRESH", "STALE", "UNKNOWN"))
RUNTIME_STATES = frozenset(("available", "degraded", "unavailable"))
LIFECYCLE_HINTS = frozenset(("legacy", "superseded"))


@dataclass(frozen=True)
class RuntimeCapabilityEvidence:
    capability_id: str
    runtime_state: str
    source: str
    evidence_ref: str
    observed_at: str
    expires_at: str | None = None
    runtime_bound: bool | None = None
    source_present: bool | None = None
    source_runtime_parity: bool | None = None
    usable_interfaces: tuple[str, ...] = field(default_factory=tuple)
    lifecycle_hint: str | None = None
    evidence_kind: str | None = None
    schema: str = EVIDENCE_SCHEMA

    def __post_init__(self) -> None:
        for field_name in (
            "capability_id",
            "runtime_state",
            "source",
            "evidence_ref",
            "observed_at",
            "schema",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if self.schema != EVIDENCE_SCHEMA:
            raise ValueError("schema must be skeleton.capability_runtime_evidence.v1")
        if self.runtime_state not in RUNTIME_STATES:
            raise ValueError("runtime_state must be available, degraded, or unavailable")
        _parse_utc_timestamp(self.observed_at, "observed_at")
        if self.expires_at is not None:
            _parse_utc_timestamp(self.expires_at, "expires_at")
        for field_name in ("runtime_bound", "source_present", "source_runtime_parity"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{field_name} must be a boolean when provided")
        if not isinstance(self.usable_interfaces, tuple) or not all(
            isinstance(item, str) and item.strip() for item in self.usable_interfaces
        ):
            raise ValueError("usable_interfaces must be a tuple of non-empty strings")
        if self.lifecycle_hint is not None and self.lifecycle_hint not in LIFECYCLE_HINTS:
            raise ValueError("lifecycle_hint must be legacy or superseded when provided")
        if self.evidence_kind is not None and (
            not isinstance(self.evidence_kind, str) or not self.evidence_kind.strip()
        ):
            raise ValueError("evidence_kind must be a non-empty string when provided")

    def freshness(self, now: datetime | None = None) -> str:
        checked_at = _coerce_now(now)
        observed_at = _parse_utc_timestamp(self.observed_at, "observed_at")
        if observed_at > checked_at:
            return "STALE"
        if (
            self.expires_at is not None
            and _parse_utc_timestamp(self.expires_at, "expires_at") <= checked_at
        ):
            return "STALE"
        return "FRESH"

    def to_dict(self, now: datetime | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": self.schema,
            "capability_id": self.capability_id,
            "runtime_state": self.runtime_state,
            "source": self.source,
            "evidence_ref": self.evidence_ref,
            "observed_at": self.observed_at,
            "freshness": self.freshness(now),
        }
        for field_name in (
            "expires_at",
            "runtime_bound",
            "source_present",
            "source_runtime_parity",
            "lifecycle_hint",
            "evidence_kind",
        ):
            value = getattr(self, field_name)
            if value is not None:
                payload[field_name] = value
        if self.usable_interfaces:
            payload["usable_interfaces"] = list(self.usable_interfaces)
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

    checked_at = _coerce_now(now)
    evidence_by_capability: dict[str, list[RuntimeCapabilityEvidence]] = {}
    for item in evidence:
        if not isinstance(item, RuntimeCapabilityEvidence):
            raise TypeError("runtime evidence must be RuntimeCapabilityEvidence instances")
        if item.capability_id not in capabilities:
            raise ValueError(
                "runtime evidence references unknown registry capabilities: "
                + item.capability_id
            )
        evidence_by_capability.setdefault(item.capability_id, []).append(item)

    records = []
    for capability_id in sorted(capabilities):
        capability = capabilities[capability_id]
        if not isinstance(capability, Mapping):
            raise ValueError(f"capability {capability_id} must be a mapping")
        capability_evidence = tuple(evidence_by_capability.get(capability_id, ()))
        records.append(
            _derive_record(
                capability_id,
                capability,
                capability_evidence,
                source_root=source_root,
                now=checked_at,
            )
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
            "registry_available_count": len(
                [item for item in records if item["declared_status"] == "available"]
            ),
            "typed_runtime_evidence_count": sum(
                len(item["runtime_evidence"]) for item in records
            ),
            "fresh_runtime_evidence_count": sum(
                1
                for item in records
                for evidence_item in item["runtime_evidence"]
                if evidence_item["freshness"] == "FRESH"
            ),
            "live_count": len(
                [item for item in records if item["effective_status"] == "LIVE"]
            ),
            "partial_count": len(
                [item for item in records if item["effective_status"] == "PARTIAL"]
            ),
            "contract_only_count": len(
                [
                    item
                    for item in records
                    if item["effective_status"] == "CONTRACT_ONLY"
                ]
            ),
            "blocked_count": len(
                [item for item in records if item["effective_status"] == "BLOCKED"]
            ),
            "legacy_count": len(
                [item for item in records if item["effective_status"] == "LEGACY"]
            ),
            "superseded_count": len(
                [item for item in records if item["effective_status"] == "SUPERSEDED"]
            ),
            "drift_count": len([item for item in records if item["drift"]]),
        },
        "capabilities": records,
    }


def _derive_record(
    capability_id: str,
    capability: Mapping[str, Any],
    evidence: tuple[RuntimeCapabilityEvidence, ...],
    *,
    source_root: str | Path | None,
    now: datetime,
) -> dict[str, Any]:
    fresh_evidence = tuple(item for item in evidence if item.freshness(now) == "FRESH")
    freshest_evidence = tuple(sorted(evidence, key=lambda item: item.observed_at))
    selected = freshest_evidence[-1] if freshest_evidence else None
    fresh_states = {item.runtime_state for item in fresh_evidence}
    lifecycle_hint = _lifecycle_hint(capability, evidence)
    registry_status = _normalized_status(capability.get("status"))
    source_present = _source_present(capability, source_root, evidence)
    runtime_bound = any(item.runtime_bound is True for item in fresh_evidence)
    source_runtime_parity = _source_runtime_parity(fresh_evidence)
    usable_interfaces = _usable_interfaces(capability, fresh_evidence)
    freshness = _record_freshness(evidence, fresh_evidence)
    reason_codes = _reason_codes(
        registry_status=registry_status,
        freshness=freshness,
        lifecycle_hint=lifecycle_hint,
        evidence=evidence,
        fresh_evidence=fresh_evidence,
        source_present=source_present,
        runtime_bound=runtime_bound,
        source_runtime_parity=source_runtime_parity,
        usable_interfaces=usable_interfaces,
    )
    effective_status = _effective_status(
        registry_status=registry_status,
        lifecycle_hint=lifecycle_hint,
        freshness=freshness,
        fresh_states=fresh_states,
        fresh_evidence=fresh_evidence,
        runtime_bound=runtime_bound,
        source_runtime_parity=source_runtime_parity,
        usable_interfaces=usable_interfaces,
    )
    drift = bool(
        fresh_evidence
        and registry_status != "available"
        and effective_status in {"LIVE", "PARTIAL", "BLOCKED"}
    )
    evidence_observed_at = selected.observed_at if selected is not None else None
    evidence_kinds = sorted(
        {
            item.evidence_kind
            for item in evidence
            if item.evidence_kind is not None
        }
    )
    if not evidence_kinds and evidence:
        evidence_kinds = ["runtime"]

    return {
        "capability_id": capability_id,
        "declared_status": registry_status or None,
        "effective_status": effective_status,
        "freshness": freshness,
        "runtime_bound": runtime_bound,
        "source_present": source_present,
        "source_runtime_parity": source_runtime_parity,
        "usable_interfaces": usable_interfaces,
        "drift": drift,
        "reason_codes": sorted(set(reason_codes)),
        "evidence_observed_at": evidence_observed_at,
        "evidence_kinds": evidence_kinds,
        "authoritative": False,
        "public_safe": True,
        "runtime_evidence": [item.to_dict(now) for item in evidence],
    }


def _effective_status(
    *,
    registry_status: str,
    lifecycle_hint: str | None,
    freshness: str,
    fresh_states: set[str],
    fresh_evidence: tuple[RuntimeCapabilityEvidence, ...],
    runtime_bound: bool,
    source_runtime_parity: bool,
    usable_interfaces: list[str],
) -> str:
    if lifecycle_hint == "superseded":
        return "SUPERSEDED"
    if lifecycle_hint == "legacy":
        return "LEGACY"
    if not fresh_evidence:
        return "CONTRACT_ONLY"
    if freshness != "FRESH":
        return "CONTRACT_ONLY"
    if "unavailable" in fresh_states and runtime_bound:
        return "BLOCKED"
    if len(fresh_states) > 1:
        return "PARTIAL"
    if (
        fresh_states == {"available"}
        and runtime_bound
        and source_runtime_parity is True
        and usable_interfaces
    ):
        return "LIVE"
    return "PARTIAL"


def _record_freshness(
    evidence: tuple[RuntimeCapabilityEvidence, ...],
    fresh_evidence: tuple[RuntimeCapabilityEvidence, ...],
) -> str:
    if not evidence:
        return "UNKNOWN"
    if len(fresh_evidence) == len(evidence):
        return "FRESH"
    return "STALE"


def _lifecycle_hint(
    capability: Mapping[str, Any],
    evidence: tuple[RuntimeCapabilityEvidence, ...],
) -> str | None:
    for item in evidence:
        if item.lifecycle_hint in LIFECYCLE_HINTS:
            return item.lifecycle_hint
    status = _normalized_status(capability.get("status"))
    if status in LIFECYCLE_HINTS:
        return status
    return None


def _source_present(
    capability: Mapping[str, Any],
    source_root: str | Path | None,
    evidence: tuple[RuntimeCapabilityEvidence, ...],
) -> bool:
    for item in evidence:
        if item.source_present is not None:
            return item.source_present
    module = capability.get("module")
    if not isinstance(module, str) or not module.strip():
        return False
    if source_root is None:
        return True
    module_path = Path(module)
    if module_path.is_absolute() or ".." in module_path.parts:
        return False
    return (Path(source_root) / module_path).exists()


def _source_runtime_parity(
    fresh_evidence: tuple[RuntimeCapabilityEvidence, ...],
) -> bool:
    if not fresh_evidence:
        return False
    return all(item.source_runtime_parity is True for item in fresh_evidence)


def _usable_interfaces(
    capability: Mapping[str, Any],
    fresh_evidence: tuple[RuntimeCapabilityEvidence, ...],
) -> list[str]:
    interfaces = sorted(
        {
            interface
            for item in fresh_evidence
            for interface in item.usable_interfaces
        }
    )
    if interfaces:
        return interfaces
    entry = capability.get("entry")
    return [entry] if isinstance(entry, str) and entry.strip() and fresh_evidence else []


def _reason_codes(
    *,
    registry_status: str,
    freshness: str,
    lifecycle_hint: str | None,
    evidence: tuple[RuntimeCapabilityEvidence, ...],
    fresh_evidence: tuple[RuntimeCapabilityEvidence, ...],
    source_present: bool,
    runtime_bound: bool,
    source_runtime_parity: bool,
    usable_interfaces: list[str],
) -> list[str]:
    codes: list[str] = []
    if registry_status == "available":
        codes.append("REGISTRY_AVAILABLE")
    elif registry_status:
        codes.append("REGISTRY_NOT_AVAILABLE")
    else:
        codes.append("REGISTRY_STATUS_UNKNOWN")
    if lifecycle_hint == "legacy":
        codes.append("LIFECYCLE_LEGACY")
    if lifecycle_hint == "superseded":
        codes.append("LIFECYCLE_SUPERSEDED")
    if freshness == "UNKNOWN":
        codes.append("NO_RUNTIME_EVIDENCE")
    elif freshness == "STALE":
        codes.append("STALE_RUNTIME_EVIDENCE")
    else:
        codes.append("FRESH_RUNTIME_EVIDENCE")
    if any(item.runtime_state == "unavailable" for item in fresh_evidence):
        codes.append("RUNTIME_UNAVAILABLE")
    if any(item.runtime_state == "degraded" for item in fresh_evidence):
        codes.append("RUNTIME_DEGRADED")
    if len({item.runtime_state for item in fresh_evidence}) > 1:
        codes.append("CONTRADICTORY_RUNTIME_EVIDENCE")
    if not source_present:
        codes.append("SOURCE_NOT_PRESENT")
    if not runtime_bound and evidence:
        codes.append("RUNTIME_NOT_BOUND")
    if not source_runtime_parity and evidence:
        codes.append("SOURCE_RUNTIME_PARITY_INCOMPLETE")
    if not usable_interfaces and evidence:
        codes.append("NO_USABLE_INTERFACES")
    return codes


def _normalized_status(registry_status: Any) -> str:
    return registry_status.strip().lower() if isinstance(registry_status, str) else ""


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
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
