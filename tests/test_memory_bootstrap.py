from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from core.memory_bootstrap import (
    MEMORY_BOOTSTRAP_RECEIPT_SCHEMA,
    MemoryBootstrap,
    MemoryBootstrapError,
    bootstrap_request_from_task,
    output_contains_private_echo,
    private_context_file,
)
from core.memory_gateway import MemoryGateway, capability_token
from core.memory_gateway_storage import PrivateMemoryGatewayStorage
from core.memory_scope_resolver import MemoryScopeError, resolve_memory_transition_scope, task_transition_hash
from core.private_memory_stack import PrivateMemoryStack


def _gateway(tmp_path: Path) -> tuple[PrivateMemoryStack, MemoryGateway]:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    stack.put(
        namespace="skeleton.operator_preferences",
        fact_id="fast_autonomous_execution_v1",
        value={
            "summary": "allowed word summary",
            "nested": {"fingerprint": "alpha-private-value-20260723"},
            "tags": ["bootstrap"],
        },
    )
    gateway = MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=False),
        private_memory_storage=PrivateMemoryGatewayStorage(stack),
    )
    return stack, gateway


def _request(task: str, **updates: object) -> dict[str, object]:
    request = bootstrap_request_from_task(
        task_content=task,
        project_id="skeleton",
        dataset_id="skeleton",
        repository="alanua/Skeleton",
        branch="runner/issue-1911",
    )
    request.update(updates)
    return request


def test_scope_resolver_accepts_real_repo_branch_and_rejects_foreign_wildcard_traversal() -> None:
    scope = resolve_memory_transition_scope(
        {
            "project_id": "skeleton",
            "dataset_id": "skeleton",
            "repository": "alanua/Skeleton",
            "branch": "runner/issue-1911",
            "task_transition_hash": "a" * 64,
        }
    )
    assert scope.repository == "alanua/Skeleton"
    assert scope.branch == "runner/issue-1911"

    for bad in (
        {"repository": "other/Skeleton"},
        {"branch": "../main"},
        {"project_id": "*"},
    ):
        payload = {
            "project_id": "skeleton",
            "dataset_id": "skeleton",
            "repository": "alanua/Skeleton",
            "branch": "runner/issue-1911",
            "task_transition_hash": "a" * 64,
        }
        payload.update(bad)
        with pytest.raises(MemoryScopeError):
            resolve_memory_transition_scope(payload)


def test_bootstrap_uses_gateway_for_canonical_status_read_and_list(tmp_path: Path) -> None:
    _stack, gateway = _gateway(tmp_path)
    calls: list[str] = []
    original = gateway.execute

    def recording_execute(request: dict[str, object]) -> dict[str, object]:
        calls.append(str(request["command"]))
        return original(request)

    gateway.execute = recording_execute  # type: ignore[method-assign]
    result = MemoryBootstrap(gateway, cache_dir=tmp_path / "cache").bootstrap(_request("exact task"))

    assert result["status"] == "READY"
    assert "skeleton.memory.private_status" in calls
    assert "skeleton.memory.private_current_revision" in calls
    assert "skeleton.memory.private_list_exact" in calls
    assert "skeleton.memory.private_read_exact" in calls


def test_public_mode_missing_storage_and_empty_exact_context_fail_closed(tmp_path: Path) -> None:
    public = MemoryGateway(capability_token(namespaces=("skeleton",), public_mode=True))
    with pytest.raises(MemoryBootstrapError) as public_exc:
        MemoryBootstrap(public, cache_dir=tmp_path / "cache").bootstrap(_request("task"))
    assert public_exc.value.reason_code == "PRIVATE_MEMORY_PUBLIC_MODE_FORBIDDEN"

    missing = MemoryGateway(capability_token(namespaces=("skeleton",), public_mode=False))
    with pytest.raises(MemoryBootstrapError) as missing_exc:
        MemoryBootstrap(missing, cache_dir=tmp_path / "cache2").bootstrap(_request("task"))
    assert missing_exc.value.reason_code == "PRIVATE_MEMORY_STORAGE_REQUIRED"

    stack = PrivateMemoryStack(tmp_path / "empty")
    stack.init(import_manifest=False)
    gateway = MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=False),
        private_memory_storage=PrivateMemoryGatewayStorage(stack),
    )
    with pytest.raises(MemoryBootstrapError) as empty_exc:
        MemoryBootstrap(gateway, cache_dir=tmp_path / "cache3").bootstrap(_request("task"))
    assert empty_exc.value.reason_code == "MemoryGatewayStorageError"


def test_exact_validation_rejects_malformed_gateway_records(tmp_path: Path) -> None:
    _stack, gateway = _gateway(tmp_path)
    original = gateway.execute

    def malformed(request: dict[str, object]) -> dict[str, object]:
        result = original(request)
        if request["command"] == "skeleton.memory.private_read_exact":
            payload = dict(result["payload"])
            payload["value_hash"] = "0" * 64
            result = {**result, "payload": payload}
        return result

    gateway.execute = malformed  # type: ignore[method-assign]
    with pytest.raises(MemoryBootstrapError) as excinfo:
        MemoryBootstrap(gateway, cache_dir=tmp_path / "cache").bootstrap(_request("task"))
    assert excinfo.value.reason_code == "EXACT_VALUE_HASH_MISMATCH"


def test_cache_keys_bind_task_hash_and_revision(tmp_path: Path) -> None:
    stack, gateway = _gateway(tmp_path)
    bootstrap = MemoryBootstrap(gateway, cache_dir=tmp_path / "cache")
    first = bootstrap.bootstrap(_request("task one"))
    repeat = bootstrap.bootstrap(_request("task one"))
    second_task = bootstrap.bootstrap(_request("task two"))
    stack.put(namespace="skeleton.operator_preferences", fact_id="fresh", value={"summary": "new"})
    changed_revision = bootstrap.bootstrap(_request("task one"))

    assert first["private_context"]["scope_cache_key"] == repeat["private_context"]["scope_cache_key"]
    assert first["private_context"]["scope_cache_key"] != second_task["private_context"]["scope_cache_key"]
    assert changed_revision["private_context"]["canonical_revision"] > first["private_context"]["canonical_revision"]


def test_private_context_file_permissions_echo_detector_and_public_receipt(tmp_path: Path) -> None:
    _stack, gateway = _gateway(tmp_path)
    result = MemoryBootstrap(gateway, cache_dir=tmp_path / "cache").bootstrap(_request("task"))
    context = result["private_context"]
    receipt = result["public_receipt"]

    with private_context_file(context) as path:
        assert path.exists()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not path.exists()

    assert output_contains_private_echo("ordinary summary text", context) is False
    assert output_contains_private_echo(str(context["echo_sentinel"]), context) is True
    assert output_contains_private_echo("alpha-private-value-20260723", context) is True
    assert receipt["schema"] == MEMORY_BOOTSTRAP_RECEIPT_SCHEMA
    serialized = json.dumps(receipt, sort_keys=True)
    assert "alanua" not in serialized
    assert "runner/issue" not in serialized
    assert "alpha-private" not in serialized
