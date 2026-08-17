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
    task_classes: tuple[str, ...]
    capabilities: tuple[str, ...]
    locality: str
    binding_kinds: tuple[str, ...]
    side_effect_classes: tuple[str, ...]
    credential_aliases: tuple[str, ...]
    health: str
    max_timeout_seconds: int
    max_concurrency: int
    required_completion_evidence: tuple[str, ...]
    supported_model_provider_families: tuple[str, ...] = ()
    embedded_model_alias: str | None = None
    embedded_model_capabilities: tuple[str, ...] = ()
    preference_rank: int = 100

    def __post_init__(self) -> None:
        if not self.executor_id or not self.family:
            raise ExecutorRegistryError("executor_identity_required")
        if not self.task_classes or not self.capabilities:
            raise ExecutorRegistryError("executor_capabilities_required")
        if self.locality not in LOCALITIES or self.health not in EXECUTOR_HEALTH:
            raise ExecutorRegistryError("invalid_executor_state")
        if not self.binding_kinds or any(kind not in BINDING_KINDS for kind in self.binding_kinds):
            raise ExecutorRegistryError("invalid_binding_kind")
        if self.max_timeout_seconds <= 0 or self.max_concurrency <= 0 or self.preference_rank < 0:
            raise ExecutorRegistryError("invalid_executor_limits")
        if "EMBEDDED_MODEL" in self.binding_kinds and not self.embedded_model_alias:
            raise ExecutorRegistryError("embedded_model_alias_required")

    def supports(
        self,
        *,
        task_class: str,
        capabilities: tuple[str, ...],
        side_effect_class: str,
        binding_kind: str,
    ) -> bool:
        return (
            self.health not in {"COOLDOWN", "DISABLED"}
            and task_class in self.task_classes
            and all(capability in self.capabilities for capability in capabilities)
            and side_effect_class in self.side_effect_classes
            and binding_kind in self.binding_kinds
        )


def executor_record_from_mapping(raw: Mapping[str, object]) -> ExecutorRecord:
    return ExecutorRecord(
        executor_id=str(raw.get("executor_id", "")),
        family=str(raw.get("family", "")),
        task_classes=tuple(str(item) for item in raw.get("task_classes", ())),
        capabilities=tuple(str(item) for item in raw.get("capabilities", ())),
        locality=str(raw.get("locality", "")),
        binding_kinds=tuple(str(item) for item in raw.get("binding_kinds", ())),
        side_effect_classes=tuple(str(item) for item in raw.get("side_effect_classes", ())),
        credential_aliases=tuple(str(item) for item in raw.get("credential_aliases", ())),
        health=str(raw.get("health", "DISABLED")),
        max_timeout_seconds=int(raw.get("max_timeout_seconds", 0)),
        max_concurrency=int(raw.get("max_concurrency", 0)),
        required_completion_evidence=tuple(str(item) for item in raw.get("required_completion_evidence", ())),
        supported_model_provider_families=tuple(str(item) for item in raw.get("supported_model_provider_families", ())),
        embedded_model_alias=(str(raw["embedded_model_alias"]) if raw.get("embedded_model_alias") else None),
        embedded_model_capabilities=tuple(str(item) for item in raw.get("embedded_model_capabilities", ())),
        preference_rank=int(raw.get("preference_rank", 100)),
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
    payload = [
        {
            "executor_id": item.executor_id,
            "family": item.family,
            "task_classes": list(item.task_classes),
            "capabilities": list(item.capabilities),
            "locality": item.locality,
            "binding_kinds": list(item.binding_kinds),
            "side_effect_classes": list(item.side_effect_classes),
            "credential_aliases": list(item.credential_aliases),
            "health": item.health,
            "max_timeout_seconds": item.max_timeout_seconds,
            "max_concurrency": item.max_concurrency,
            "required_completion_evidence": list(item.required_completion_evidence),
            "supported_model_provider_families": list(item.supported_model_provider_families),
            "embedded_model_alias": item.embedded_model_alias,
            "embedded_model_capabilities": list(item.embedded_model_capabilities),
            "preference_rank": item.preference_rank,
        }
        for item in sorted(records, key=lambda value: value.executor_id)
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
