from __future__ import annotations

from pathlib import Path

import pytest

from core.executor_registry import ExecutorRecord, ExecutorRegistryError, load_executor_registry, registry_snapshot_hash


ROOT = Path(__file__).resolve().parents[1]


def test_registry_loads_three_distinct_executor_kinds() -> None:
    records = load_executor_registry(ROOT / "EXECUTOR_REGISTRY.yaml")
    by_id = {record.executor_id: record for record in records}

    assert set(by_id) == {"deterministic-maintenance", "codex-cli", "openhands-cli"}
    assert by_id["deterministic-maintenance"].binding_kinds == ("NO_MODEL",)
    assert by_id["codex-cli"].binding_kinds == ("EMBEDDED_MODEL",)
    assert by_id["openhands-cli"].binding_kinds == ("EXTERNAL_MODEL",)
    assert by_id["openhands-cli"].compatible_model_provider_families == ("openrouter", "local")


def test_private_local_rejects_cloud_only_codegen_executors() -> None:
    records = load_executor_registry(ROOT / "EXECUTOR_REGISTRY.yaml")
    codegen = [record for record in records if "code_generation" in record.supported_task_classes]

    assert all(not record.supports_task("code_generation", ("repository_read",), "REPOSITORY_WRITE", "PRIVATE_LOCAL") for record in codegen)


def test_external_model_executor_requires_declared_provider_family() -> None:
    with pytest.raises(ExecutorRegistryError, match="external_model_executor_requires_provider_family"):
        ExecutorRecord(
            executor_id="bad",
            family="bad",
            supported_task_classes=("code_generation",),
            capabilities=("repository_read",),
            locality="LOCAL",
            privacy_classes=("PUBLIC",),
            binding_kinds=("EXTERNAL_MODEL",),
            side_effect_classes=("REPOSITORY_WRITE",),
            credential_aliases=(),
            health="LIVE",
            timeout_seconds=10,
            max_concurrency=1,
            completion_evidence=("validation",),
        )


def test_executor_registry_snapshot_hash_is_deterministic() -> None:
    records = load_executor_registry(ROOT / "EXECUTOR_REGISTRY.yaml")
    assert registry_snapshot_hash(records) == registry_snapshot_hash(tuple(reversed(records)))
