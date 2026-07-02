from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.task_memory_context import TaskMemoryContextError, build_task_memory_context
from core.private_memory_stack import PrivateMemoryStack


def test_context_results_require_exact_canonical_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stack = _context_stack(tmp_path)

    def missing_exact(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("exact confirmation failed")

    monkeypatch.setattr(stack, "get", missing_exact)

    with pytest.raises(RuntimeError):
        build_task_memory_context(
            stack,
            project_id="skeleton",
            task_route="runner",
            profile="private_runtime",
            query="alpha",
            required=True,
        )


def test_public_control_includes_only_egress_approved_safe_records(tmp_path: Path) -> None:
    stack = _context_stack(tmp_path)

    result = build_task_memory_context(
        stack,
        project_id="skeleton",
        task_route="runner",
        profile="public_control",
        query="alpha",
        namespaces=["skeleton.context"],
        required=True,
    )
    receipt = result.public_receipt()
    serialized = json.dumps(receipt, sort_keys=True)

    assert receipt["counts"]["selected"] == 1
    assert receipt["selected_records"][0]["public_text"] == "alpha safe control"
    assert "runtime only alpha" not in serialized
    assert result.private_values == []


def test_private_runtime_values_never_enter_public_receipt(tmp_path: Path) -> None:
    stack = _context_stack(tmp_path)

    result = build_task_memory_context(
        stack,
        project_id="skeleton",
        task_route="runner",
        profile="private_runtime",
        query="alpha",
        required=True,
    )
    receipt_json = json.dumps(result.public_receipt(), sort_keys=True)

    assert result.private_values
    assert "runtime only alpha" not in receipt_json
    assert "selected_canonical_refs" in result.public_receipt()


def test_required_blocks_on_non_ready_stack_and_optional_returns_unavailable(tmp_path: Path) -> None:
    stack = _context_stack(tmp_path)
    (tmp_path / "mempalace.index.json").unlink()

    with pytest.raises(TaskMemoryContextError):
        build_task_memory_context(
            stack,
            project_id="skeleton",
            task_route="runner",
            profile="private_runtime",
            query="alpha",
            required=True,
        )

    optional = build_task_memory_context(
        stack,
        project_id="skeleton",
        task_route="runner",
        profile="private_runtime",
        query="alpha",
        required=False,
    )
    assert optional.public_receipt()["status"] == "UNAVAILABLE"
    assert optional.private_values == []


def test_deterministic_limits_truncation_and_context_hash(tmp_path: Path) -> None:
    stack = _context_stack(tmp_path)

    first = build_task_memory_context(
        stack,
        project_id="skeleton",
        task_route="runner",
        profile="public_control",
        query="alpha",
        required=True,
        limit=1,
        max_chars=5,
    ).public_receipt()
    second = build_task_memory_context(
        stack,
        project_id="skeleton",
        task_route="runner",
        profile="public_control",
        query="alpha",
        required=True,
        limit=1,
        max_chars=5,
    ).public_receipt()

    assert first["selected_records"][0]["public_text"] == "alpha"
    assert first["truncated"] is True
    assert first["context_hash"] == second["context_hash"]


def test_public_control_rejects_secret_like_egress_payload(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    stack.put(
        namespace="skeleton.context",
        fact_id="bad",
        value={"egress_classification": "PUBLIC_SAFE_CONTROL", "text": "password marker", "tags": ["marker"]},
    )

    with pytest.raises(TaskMemoryContextError):
        build_task_memory_context(
            stack,
            project_id="skeleton",
            task_route="runner",
            profile="public_control",
            query="marker",
            required=True,
        )


def _context_stack(tmp_path: Path) -> PrivateMemoryStack:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    stack.put(
        namespace="skeleton.context",
        fact_id="public",
        value={"egress_classification": "PUBLIC_SAFE_CONTROL", "text": "alpha safe control", "tags": ["alpha"]},
    )
    stack.put(
        namespace="skeleton.context",
        fact_id="private",
        value={"summary": "runtime only alpha", "tags": ["runtime"]},
    )
    return stack
