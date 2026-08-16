from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Mapping


BINDING_KINDS = frozenset({"NO_MODEL", "EMBEDDED_MODEL", "EXTERNAL_MODEL"})
EXECUTOR_HEALTH = frozenset({"LIVE", "DEGRADED", "COOLDOWN", "DISABLED"})
LOCALITIES = frozenset({"LOCAL", "CLOUD", "HYBRID"})


class ExecutorRegistryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ExecutorRecord:
    executor_id: str
    family: str
    supported_task_classes: tuple[str, ...]
    capabilities: tuple[str, ...]
    locality: str
    privacy_classes: tuple[str, ...]
    binding_kinds: tuple[str, ...]
    side_effect_classes: tuple[str, ...]
    credential_aliases: tuple[str, ...]
    health: str
    timeout_seconds: int
    max_concurrency: int
    completion_evidence: tuple[str, ...]
    compatible_model_provider_families: tuple[str, ...] = ()
    embedded_model_capabilities: Mapping[str, float] | None = None
    priority_rank: int = 100

    def __post_init__(self) -> None:
        if not self.executor_id or not self.family:
            raise ExecutorRegistryError("executor_identity_required")
        if not self.supported_task_classes or not self.capabilities:
            raise ExecutorRegistryError("executor_capabilities_required")
        if self.locality not in LOCALITIES:
            raise ExecutorRegistryError("invalid_executor_locality")
        if not self.privacy_classes:
            raise ExecutorRegistryError("executor_privacy_required")
        if not self.binding_kinds or any(kind not in BINDING_KINDS for kind in self.binding_kinds):
            raise ExecutorRegistryError("invalid_binding_kind")
        if self.health not in EXECUTOR_HEALTH:
            raise ExecutorRegistryError("invalid_executor_health")
        if self.timeout_seconds <= 0 or self.max_concurrency <= 0 or self.priority_rank < 0:
            raise ExecutorRegistryError("invalid_executor_limits")
        embedded = self.embedded_model_capabilities or {}
        for capability_id, score in embedded.items():
            if not capability_id or not 0.0 <= float(score) <= 1.0:
                raise ExecutorRegistryError("invalid_embedded_model_capability")
        if "EXTERNAL_MODEL" in self.binding_kinds and not self.compatible_model_provider_families:
            raise ExecutorRegistryError("external_model_executor_requires_provider_family")

    def supports_task(self, task_class: str, capabilities: tuple[str, ...], side_effect_class: str, privacy_class: str) -> bool:
        return (
            self.health not in {"COOLDOWN", "DISABLED"}
            and task_class in self.supported_task_classes
            and set(capabilities).issubset(self.capabilities)
            and side_effect_class in self.side_effect_classes
            and privacy_class in self.privacy_classes
        )


def executor_record_from_mapping(raw: Mapping[str, object]) -> ExecutorRecord:
    embedded_raw = raw.get("embedded_model_capabilities", {})
    if not isinstance(embedded_raw, Mapping):
        raise ExecutorRegistryError("embedded_capabilities_mapping_required")
    return ExecutorRecord(
        executor_id=str(raw.get("executor_id", "")),
        family=str(raw.get("family", "")),
        supported_task_classes=tuple(str(item) for item in raw.get("supported_task_classes", ())),
        capabilities=tuple(str(item) for item in raw.get("capabilities", ())),
        locality=str(raw.get("locality", "")),
        privacy_classes=tuple(str(item) for item in raw.get("privacy_classes", ())),
        binding_kinds=tuple(str(item) for item in raw.get("binding_kinds", ())),
        side_effect_classes=tuple(str(item) for item in raw.get("side_effect_classes", ())),
        credential_aliases=tuple(str(item) for item in raw.get("credential_aliases", ())),
        health=str(raw.get("health", "DISABLED")),
        timeout_seconds=int(raw.get("timeout_seconds", 0)),
        max_concurrency=int(raw.get("max_concurrency", 0)),
        completion_evidence=tuple(str(item) for item in raw.get("completion_evidence", ())),
        compatible_model_provider_families=tuple(
            str(item) for item in raw.get("compatible_model_provider_families", ())
        ),
        embedded_model_capabilities={str(key): float(value) for key, value in embedded_raw.items()},
        priority_rank=int(raw.get("priority_rank", 100)),
    )


def registry_from_mapping(raw: Mapping[str, object]) -> tuple[ExecutorRecord, ...]:
    values = raw.get("executors")
    if not isinstance(values, list):
        raise ExecutorRegistryError("executors_list_required")
    records = tuple(executor_record_from_mapping(item) for item in values if isinstance(item, Mapping))
    if len(records) != len(values):
        raise ExecutorRegistryError("invalid_executor_record")
    ids = [record.executor_id for record in records]
    if len(ids) != len(set(ids)):
        raise ExecutorRegistryError("duplicate_executor_id")
    return records


def load_executor_registry(path: str | Path) -> tuple[ExecutorRecord, ...]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutorRegistryError("registry_parse_failed") from exc
    if not isinstance(raw, Mapping):
        raise ExecutorRegistryError("registry_mapping_required")
    return registry_from_mapping(raw)


def registry_snapshot_hash(records: tuple[ExecutorRecord, ...]) -> str:
    public = [
        {
            "executor_id": record.executor_id,
            "family": record.family,
            "supported_task_classes": list(record.supported_task_classes),
            "capabilities": list(record.capabilities),
            "locality": record.locality,
            "privacy_classes": list(record.privacy_classes),
            "binding_kinds": list(record.binding_kinds),
            "side_effect_classes": list(record.side_effect_classes),
            "credential_aliases": list(record.credential_aliases),
            "health": record.health,
            "timeout_seconds": record.timeout_seconds,
            "max_concurrency": record.max_concurrency,
            "completion_evidence": list(record.completion_evidence),
            "compatible_model_provider_families": list(record.compatible_model_provider_families),
            "embedded_model_capabilities": dict(sorted((record.embedded_model_capabilities or {}).items())),
            "priority_rank": record.priority_rank,
        }
        for record in sorted(records, key=lambda item: item.executor_id)
    ]
    encoded = json.dumps(public, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
