from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from core.cognee_projection_adapter import CogneeProjectionAdapter, DisposableInMemoryCogneeBackend
from core.memory_bootstrap import (
    MEMORY_BOOTSTRAP_REQUEST_SCHEMA,
    PRIVATE_CONTEXT_ENV,
    PRIVATE_CONTEXT_MARKER,
    MemoryBootstrap,
    reset_bootstrap_adapter_cache,
)
from core.memory_scope_resolver import task_transition_hash
from core.private_memory_stack import PrivateMemoryStack


_DEFAULT_PROVENANCE = object()
_MISSING_PROVENANCE = object()


def _request(root: Path, canonical_ref: str, task: str = "exact task body") -> dict[str, object]:
    return {
        "schema": MEMORY_BOOTSTRAP_REQUEST_SCHEMA,
        "mandatory": True,
        "private_root": str(root),
        "scope": {
            "project_id": "skeleton",
            "dataset_id": "issue1917",
            "repository": "alanua/Skeleton",
            "branch": "runner/issue-1917-memory-final",
            "task_transition_hash": task_transition_hash(task),
        },
        "canonical_refs": [canonical_ref],
        "query": "ventilation",
        "repository_root": str(Path.cwd()),
        "worktree_root": str(Path.cwd()),
    }


def _canonical_source_attribution(exact: dict[str, object]) -> dict[str, object]:
    return {
        "canonical_ref": exact["canonical_ref"],
        "canonical_revision": exact["canonical_revision"],
        "source_kind": "canonical_sqlite",
        "value_hash": exact["value_hash"],
    }


class _ConfirmedCogneeAdapter:
    def __init__(
        self,
        exact: dict[str, object],
        *,
        bounded_text: str = "AlphaName ventilation summary",
        provenance: object = _DEFAULT_PROVENANCE,
    ) -> None:
        self.exact = exact
        self.bounded_text = bounded_text
        self.provenance = [_canonical_source_attribution(exact)] if provenance is _DEFAULT_PROVENANCE else provenance
        self.recall_calls = 0

    def health(self, *, project_id: object, dataset_id: object, current_canonical_revision: int) -> dict[str, object]:
        return {
            "status": "READY",
            "project_id": project_id,
            "dataset_id": dataset_id,
            "canonical_revisions": {
                "indexed_canonical_revision": current_canonical_revision,
                "current_canonical_revision": current_canonical_revision,
            },
            "authoritative": False,
        }

    def recall(self, _request_payload: dict[str, object]) -> dict[str, object]:
        self.recall_calls += 1
        result = {
            "canonical_ref": self.exact["canonical_ref"],
            "canonical_revision": self.exact["canonical_revision"],
            "content_hash": self.exact["value_hash"],
            "bounded_text": self.bounded_text,
        }
        if self.provenance is not _MISSING_PROVENANCE:
            result["provenance"] = self.provenance
        return {
            "project_id": "skeleton",
            "dataset_id": "issue1917",
            "current_canonical_revision": self.exact["canonical_revision"],
            "indexed_canonical_revision": self.exact["canonical_revision"],
            "authoritative": False,
            "results": [result],
        }


def test_bootstrap_real_private_stack_gateway_e2e_and_privacy_handoff(tmp_path: Path) -> None:
    reset_bootstrap_adapter_cache()
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    stack.put(
        namespace="skeleton.notes",
        fact_id="handoff",
        value={"summary": "AlphaName ventilation summary", "address": "42 Test Lane", "tags": ["ventilation"]},
    )
    exact = stack.get(namespace="skeleton.notes", fact_id="handoff")
    adapter = _ConfirmedCogneeAdapter(exact)
    seen: dict[str, object] = {}

    def executor(argv: list[str], stdin_text: str, env: object) -> tuple[int, str]:
        env_map = dict(env)  # type: ignore[arg-type]
        private_path = Path(env_map[PRIVATE_CONTEXT_ENV])
        payload = json.loads(private_path.read_text(encoding="utf-8"))
        seen["argv"] = argv
        seen["stdin"] = stdin_text
        seen["private_path"] = private_path
        seen["mode"] = stat.S_IMODE(private_path.stat().st_mode)
        seen["payload"] = payload
        assert payload["semantic"]["selected"] == "cognee"
        assert payload["graph"]["selected"] == "graphify"
        return 0, "summary allowed"

    receipt = MemoryBootstrap.from_request(
        _request(tmp_path, str(exact["canonical_ref"])),
        cognee_adapter_factory=lambda: adapter,
    ).execute(task_body="exact task body", executor=executor)

    assert receipt["status"] == "DONE"
    assert receipt["aggregate_counts"]["canonical_count"] == 1
    assert seen["stdin"] == "exact task body"
    assert "exact task body" not in seen["argv"]
    assert str(seen["private_path"]) not in seen["argv"]
    assert seen["mode"] == 0o600
    assert not Path(seen["private_path"]).exists()
    assert not str(seen["private_path"]).startswith(str(Path.cwd()))


def test_bootstrap_rejects_stale_cognee_and_uses_fresh_mempalace(tmp_path: Path) -> None:
    reset_bootstrap_adapter_cache()
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    stack.put(namespace="skeleton.notes", fact_id="fallback", value={"summary": "ventilation fallback"})
    exact = stack.get(namespace="skeleton.notes", fact_id="fallback")
    stale_adapter = CogneeProjectionAdapter(DisposableInMemoryCogneeBackend())
    captured: dict[str, object] = {}

    def executor(_argv: list[str], _stdin_text: str, env: object) -> tuple[int, str]:
        captured.update(json.loads(Path(dict(env)[PRIVATE_CONTEXT_ENV]).read_text(encoding="utf-8")))  # type: ignore[arg-type]
        return 0, "done"

    receipt = MemoryBootstrap.from_request(
        _request(tmp_path, str(exact["canonical_ref"])),
        cognee_adapter_factory=lambda: stale_adapter,
    ).execute(task_body="exact task body", executor=executor)

    assert receipt["status"] == "DONE"
    assert captured["semantic"]["selected"] == "mempalace"


def test_bootstrap_blocks_marker_and_nested_private_value_echo(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    stack.put(namespace="skeleton.notes", fact_id="echo", value={"summary": {"name": "AlphaName"}})
    exact = stack.get(namespace="skeleton.notes", fact_id="echo")

    def executor(_argv: list[str], _stdin_text: str, _env: object) -> tuple[int, str]:
        return 0, f"{PRIVATE_CONTEXT_MARKER} AlphaName"

    receipt = MemoryBootstrap.from_request(_request(tmp_path, str(exact["canonical_ref"]))).execute(
        task_body="exact task body",
        executor=executor,
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_codes"] == ["PRIVATE_CONTEXT_ECHO_BLOCKED"]
    assert "AlphaName" not in json.dumps(receipt)


def test_bootstrap_allows_public_metadata_echo_without_private_value(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    stack.put(namespace="skeleton.notes", fact_id="safe_echo", value={"summary": "AlphaName private"})
    exact = stack.get(namespace="skeleton.notes", fact_id="safe_echo")

    def executor(_argv: list[str], _stdin_text: str, _env: object) -> tuple[int, str]:
        return (
            0,
            "skeleton alanua/Skeleton issue1917 runner/issue-1917-memory-final "
            "skeleton.memory_bootstrap.response.v1 skeleton.notes:safe_echo canonical cognee mempalace graphify summary",
        )

    receipt = MemoryBootstrap.from_request(_request(tmp_path, str(exact["canonical_ref"]))).execute(
        task_body="exact task body",
        executor=executor,
    )

    assert receipt["status"] == "DONE"


@pytest.mark.parametrize(
    "provenance",
    [
        _MISSING_PROVENANCE,
        [],
        [{"canonical_ref": "skeleton.notes:foreign", "canonical_revision": 1, "source_kind": "canonical_sqlite", "value_hash": "0" * 64}],
    ],
)
def test_bootstrap_rejects_cognee_missing_empty_or_foreign_provenance(tmp_path: Path, provenance: object) -> None:
    reset_bootstrap_adapter_cache()
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    stack.put(namespace="skeleton.notes", fact_id="cognee_provenance", value={"summary": "ventilation cognee"})
    exact = stack.get(namespace="skeleton.notes", fact_id="cognee_provenance")
    adapter = _ConfirmedCogneeAdapter(exact, provenance=provenance)

    context = MemoryBootstrap.from_request(
        _request(tmp_path, str(exact["canonical_ref"])),
        cognee_adapter_factory=lambda: adapter,
    ).build_private_context()

    assert context["semantic"]["selected"] == "mempalace"


@pytest.mark.parametrize(
    "provenance",
    [
        _MISSING_PROVENANCE,
        [],
        [{"canonical_ref": "skeleton.notes:foreign", "canonical_revision": 1, "source_kind": "canonical_sqlite", "value_hash": "0" * 64}],
    ],
)
def test_bootstrap_rejects_mempalace_missing_empty_or_foreign_provenance(tmp_path: Path, provenance: object) -> None:
    reset_bootstrap_adapter_cache()
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    stack.put(namespace="skeleton.notes", fact_id="mempalace_provenance", value={"summary": "ventilation mempalace"})
    exact = stack.get(namespace="skeleton.notes", fact_id="mempalace_provenance")
    bootstrap = MemoryBootstrap.from_request(
        _request(tmp_path, str(exact["canonical_ref"])),
        cognee_adapter_factory=lambda: CogneeProjectionAdapter(DisposableInMemoryCogneeBackend()),
    )
    original_gateway = bootstrap._gateway

    def malformed_gateway(suffix: str, payload: object) -> dict[str, object]:
        response = original_gateway(suffix, payload)  # type: ignore[arg-type]
        if suffix == "memory.private_search_semantic":
            response = dict(response)
            results = []
            for result in response["payload"].get("results", []):  # type: ignore[union-attr]
                rendered = dict(result)
                if provenance is _MISSING_PROVENANCE:
                    rendered.pop("source_attribution", None)
                else:
                    rendered["source_attribution"] = provenance
                results.append(rendered)
            response["payload"] = {**response["payload"], "results": results}
        return response

    bootstrap._gateway = malformed_gateway  # type: ignore[method-assign]

    context = bootstrap.build_private_context()

    assert context["semantic"]["selected"] == "none"


@pytest.mark.parametrize(
    "provenance",
    [
        _MISSING_PROVENANCE,
        [],
        [{"canonical_ref": "skeleton.notes:foreign", "canonical_revision": 1, "source_kind": "canonical_sqlite", "value_hash": "0" * 64}],
    ],
)
def test_bootstrap_rejects_graphify_missing_empty_or_foreign_provenance(tmp_path: Path, provenance: object) -> None:
    reset_bootstrap_adapter_cache()
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    stack.put(namespace="skeleton.notes", fact_id="graphify_provenance", value={"summary": "ventilation graphify"})
    exact = stack.get(namespace="skeleton.notes", fact_id="graphify_provenance")
    bootstrap = MemoryBootstrap.from_request(_request(tmp_path, str(exact["canonical_ref"])))
    original_gateway = bootstrap._gateway

    def malformed_gateway(suffix: str, payload: object) -> dict[str, object]:
        response = original_gateway(suffix, payload)  # type: ignore[arg-type]
        if suffix == "graph.private_query":
            response = dict(response)
            results = []
            for result in response["payload"].get("results", []):  # type: ignore[union-attr]
                rendered = dict(result)
                if provenance is _MISSING_PROVENANCE:
                    rendered.pop("source_attribution", None)
                else:
                    rendered["source_attribution"] = provenance
                results.append(rendered)
            response["payload"] = {**response["payload"], "results": results}
        return response

    bootstrap._gateway = malformed_gateway  # type: ignore[method-assign]

    context = bootstrap.build_private_context()

    assert context["graph"]["selected"] == "none"


@pytest.mark.parametrize(
    "scope_update",
    [
        {"project_id": "*"},
        {"dataset_id": "../other"},
        {"repository": "alanua/Skeleton/extra"},
        {"branch": "../main"},
    ],
)
def test_bootstrap_malformed_wildcard_traversal_scope_fails_closed(tmp_path: Path, scope_update: dict[str, str]) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    stack.put(namespace="skeleton.notes", fact_id="scope", value={"summary": "scope"})
    exact = stack.get(namespace="skeleton.notes", fact_id="scope")
    request = _request(tmp_path, str(exact["canonical_ref"]))
    request["scope"] = {**request["scope"], **scope_update}  # type: ignore[index]

    with pytest.raises(Exception):
        MemoryBootstrap.from_request(request).build_private_context()


def test_bootstrap_unknown_error_maps_to_generic_allowlisted_reason(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    stack.put(namespace="skeleton.notes", fact_id="unknown", value={"summary": "unknown"})
    exact = stack.get(namespace="skeleton.notes", fact_id="unknown")

    def executor(_argv: list[str], _stdin_text: str, _env: object) -> tuple[int, str]:
        raise RuntimeError("private detail")

    receipt = MemoryBootstrap.from_request(_request(tmp_path, str(exact["canonical_ref"]))).execute(
        task_body="exact task body",
        executor=executor,
    )

    assert receipt["reason_codes"] == ["INTERNAL_ERROR"]
    assert "private detail" not in json.dumps(receipt)


def test_bootstrap_context_cache_hit_corruption_miss_and_fresh_sentinel(tmp_path: Path) -> None:
    reset_bootstrap_adapter_cache()
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    stack.put(namespace="skeleton.notes", fact_id="cache", value={"summary": "ventilation cache"})
    exact = stack.get(namespace="skeleton.notes", fact_id="cache")
    adapter = _ConfirmedCogneeAdapter(exact, bounded_text="ventilation cache")
    bootstrap = MemoryBootstrap.from_request(
        _request(tmp_path, str(exact["canonical_ref"])),
        cognee_adapter_factory=lambda: adapter,
    )

    first = bootstrap.build_private_context()
    recall_calls = adapter.recall_calls
    second = bootstrap.build_private_context()
    assert adapter.recall_calls == recall_calls
    cache_files = list((tmp_path / "bootstrap_context_cache").glob("*.json"))
    cache_files[0].write_text("{not-json", encoding="utf-8")
    third = bootstrap.build_private_context()

    assert second["echo_sentinel"] != first["echo_sentinel"]
    assert third["echo_sentinel"] not in {first["echo_sentinel"], second["echo_sentinel"]}
    assert adapter.recall_calls == recall_calls + 1


def test_bootstrap_rejects_malformed_exact_provenance(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    stack.put(namespace="skeleton.notes", fact_id="bad_provenance", value={"summary": "bad provenance"})
    exact = stack.get(namespace="skeleton.notes", fact_id="bad_provenance")
    bootstrap = MemoryBootstrap.from_request(_request(tmp_path, str(exact["canonical_ref"])))
    original_gateway = bootstrap._gateway

    def malformed_gateway(suffix: str, payload: object) -> dict[str, object]:
        response = original_gateway(suffix, payload)  # type: ignore[arg-type]
        if suffix == "memory.private_read_exact":
            response = dict(response)
            response["payload"] = {**response["payload"], "provenance_refs": [{"ref": exact["canonical_ref"], "kind": "exact_source", "evidence_hash": "0" * 64}]}
        return response

    bootstrap._gateway = malformed_gateway  # type: ignore[method-assign]

    receipt = bootstrap.execute(task_body="exact task body", executor=lambda *_args: (0, "done"))

    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_codes"] == ["PRIVATE_MEMORY_NOT_READY"]


@pytest.mark.parametrize("code,raises", [(1, False), (0, True)])
def test_bootstrap_private_file_deleted_after_nonzero_and_exception(
    tmp_path: Path, code: int, raises: bool
) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    stack.put(namespace="skeleton.notes", fact_id="cleanup", value={"summary": "cleanup"})
    exact = stack.get(namespace="skeleton.notes", fact_id="cleanup")
    seen: dict[str, Path] = {}

    def executor(_argv: list[str], _stdin_text: str, env: object) -> tuple[int, str]:
        seen["path"] = Path(dict(env)[PRIVATE_CONTEXT_ENV])  # type: ignore[arg-type]
        if raises:
            raise RuntimeError("synthetic private exception")
        return code, "blocked"

    receipt = MemoryBootstrap.from_request(_request(tmp_path, str(exact["canonical_ref"]))).execute(
        task_body="exact task body",
        executor=executor,
    )

    assert receipt["status"] == "BLOCKED"
    assert not seen["path"].exists()
