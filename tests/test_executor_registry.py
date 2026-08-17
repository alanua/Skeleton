from __future__ import annotations

from pathlib import Path

import pytest

from core.executor_registry import ExecutorRegistryError, load_executor_registry, registry_from_mapping, registry_snapshot_hash


ROOT = Path(__file__).resolve().parents[1]


def test_executor_registry_loads_and_is_deterministic() -> None:
    records = load_executor_registry(ROOT / "EXECUTOR_REGISTRY.yaml")
    assert {item.executor_id for item in records} == {
        "deterministic-maintenance",
        "codex-embedded",
        "openhands-external",
    }
    assert registry_snapshot_hash(records) == registry_snapshot_hash(tuple(reversed(records)))


def test_embedded_executor_requires_alias() -> None:
    with pytest.raises(ExecutorRegistryError):
        registry_from_mapping(
            {
                "executors": [
                    {
                        "executor_id": "bad",
                        "family": "bad",
                        "task_classes": ["code_generation"],
                        "capabilities": ["repository_write"],
                        "locality": "CLOUD",
                        "privacy_classes": ["PUBLIC"],
                        "binding_kinds": ["EMBEDDED_MODEL"],
                        "side_effect_classes": ["REPOSITORY_MUTATION"],
                        "credential_aliases": [],
                        "health": "LIVE",
                        "max_timeout_seconds": 60,
                        "max_concurrency": 1,
                        "required_completion_evidence": ["deliverable_validation"],
                    }
                ]
            }
        )
