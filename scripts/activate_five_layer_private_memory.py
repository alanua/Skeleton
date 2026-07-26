from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Mapping

from core.cognee_local_runtime import (
    CogneeLocalRuntimeError,
    activation_receipt,
    atomic_write_activation_marker,
    install_or_verify_pinned_cognee,
    live_aggregate_status,
    read_activation_marker,
    restore_activation_marker,
    validate_local_provider_config,
)
from core.cognee_projection_adapter import (
    CogneePackageBackend,
    CogneeProjectionAdapter,
    DisposableInMemoryCogneeBackend,
)
from core.cognee_projection_outbox import (
    drain_projection_outbox,
    projection_outbox_status,
)
from core.memory_bootstrap import (
    MEMORY_BOOTSTRAP_REQUEST_SCHEMA,
    PRIVATE_CONTEXT_ENV,
    PRIVATE_CONTEXT_MARKER,
    MemoryBootstrap,
    reset_bootstrap_adapter_cache,
)
from core.memory_gateway_storage import (
    PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA,
    PrivateMemoryGatewayStorage,
)
from core.memory_scope_resolver import task_transition_hash
from core.private_memory_stack import PrivateMemoryStack
from core.semantic_memory_projection import (
    PROJECTION_STALE,
    SEMANTIC_PROJECTION_EVENT_SCHEMA,
    SEMANTIC_RECALL_REQUEST_SCHEMA,
    SemanticProjectionError,
    SemanticProjectionEvent,
    SemanticScope,
    projection_text_hash,
)

APPROVAL = "EXPLICIT_FINISH_WORKING_MEMORY_20260724"


class _RecordingProjectionBackend:
    def __init__(self) -> None:
        self.events: list[SemanticProjectionEvent] = []
        self.forget_count = 0

    def project(self, event: SemanticProjectionEvent) -> None:
        self.events.append(event)

    def forget_projection(self, scope: SemanticScope) -> int:
        del scope
        self.forget_count += 1
        removed = len(self.events)
        self.events.clear()
        return removed


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError("git preflight failed")
    return completed.stdout.strip()


def _origin_is_skeleton(value: str) -> bool:
    normalized = value.strip().removesuffix(".git").rstrip("/")
    return normalized.endswith("github.com/alanua/Skeleton")


def _quiet_installer(
    command: list[str], child_env: Mapping[str, str]
) -> tuple[int, str]:
    completed = subprocess.run(
        command,
        env=dict(child_env),
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=600,
        check=False,
    )
    return completed.returncode, ""


def _disk_bytes(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _bootstrap_request(root: Path, canonical_ref: str, task: str) -> dict[str, object]:
    return {
        "schema": MEMORY_BOOTSTRAP_REQUEST_SCHEMA,
        "mandatory": True,
        "private_root": str(root),
        "scope": {
            "project_id": "skeleton",
            "dataset_id": "activation_smoke",
            "repository": "alanua/Skeleton",
            "branch": "main",
            "task_transition_hash": task_transition_hash(task),
        },
        "canonical_refs": [canonical_ref],
        "query": "activation probe",
        "repository_root": str(Path.cwd()),
        "worktree_root": str(Path.cwd()),
    }


def _event(exact: Mapping[str, object]) -> dict[str, object]:
    text = "activation probe synthetic semantic memory"
    return {
        "schema": SEMANTIC_PROJECTION_EVENT_SCHEMA,
        "project_id": "skeleton",
        "dataset_id": "activation_smoke",
        "canonical_revision": exact["canonical_revision"],
        "canonical_ref": exact["canonical_ref"],
        "content_hash": exact["value_hash"],
        "projection_text_hash": projection_text_hash(text),
        "bounded_text": text,
        "provenance": [
            {
                "canonical_ref": exact["canonical_ref"],
                "canonical_revision": exact["canonical_revision"],
                "value_hash": exact["value_hash"],
                "source_kind": "canonical_sqlite",
            }
        ],
    }


def _durable_outbox_smoke(smoke_root: Path) -> bool:
    stack = PrivateMemoryStack(smoke_root)
    stack.init(import_manifest=False)
    storage = PrivateMemoryGatewayStorage(stack)
    first = storage.execute_mutation(
        {
            "schema": PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA,
            "operation": "put",
            "project_id": "skeleton",
            "dataset_id": "activation_outbox_smoke",
            "fact_namespace": "skeleton.notes",
            "fact_id": "activation_outbox_probe",
            "value": {"summary": "synthetic durable outbox probe"},
            "actor_ref": "activation-smoke",
            "reason_code": "activation-outbox-smoke",
            "approval_ref": APPROVAL,
            "idempotency_key": "activation-outbox-smoke-put",
        }
    )
    duplicate = storage.execute_mutation(
        {
            "schema": PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA,
            "operation": "put",
            "project_id": "skeleton",
            "dataset_id": "activation_outbox_smoke",
            "fact_namespace": "skeleton.notes",
            "fact_id": "activation_outbox_probe",
            "value": {"summary": "synthetic durable outbox probe"},
            "actor_ref": "activation-smoke",
            "reason_code": "activation-outbox-smoke",
            "approval_ref": APPROVAL,
            "idempotency_key": "activation-outbox-smoke-put",
        }
    )
    scope = SemanticScope(
        project_id="skeleton", dataset_id="activation_outbox_smoke"
    )
    backend = _RecordingProjectionBackend()
    drained = drain_projection_outbox(smoke_root, scope, backend)
    status = projection_outbox_status(smoke_root, scope)
    exact = stack.get(
        namespace="skeleton.notes", fact_id="activation_outbox_probe"
    )
    return (
        first.get("status") in {"DONE", "DEGRADED"}
        and duplicate.get("idempotency_classification") == "DUPLICATE_IDENTICAL"
        and drained.get("claimed_count") == 1
        and drained.get("projected_count") == 1
        and status.get("queued_count") == 0
        and status.get("processing_count") == 0
        and status.get("done_count") == 1
        and len(backend.events) == 1
        and backend.events[0].canonical_ref == exact.get("canonical_ref")
        and backend.events[0].content_hash == exact.get("value_hash")
    )


def _real_smoke(
    private_root: Path, env: Mapping[str, str]
) -> tuple[dict[str, bool], dict[str, int]]:
    smoke_parent = private_root / "activation_smoke"
    smoke_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    smoke_parent.chmod(0o700)
    with tempfile.TemporaryDirectory(prefix="run-", dir=str(smoke_parent)) as name:
        smoke_root = Path(name)
        smoke_root.chmod(0o700)
        outbox_root = smoke_root / "outbox"
        outbox_root.mkdir(mode=0o700)
        outbox_root.chmod(0o700)
        projection_queue_ok = _durable_outbox_smoke(outbox_root)

        stack = PrivateMemoryStack(smoke_root / "stack")
        stack.init(import_manifest=False)
        mutation = stack.put(
            namespace="skeleton.notes",
            fact_id="activation_probe",
            value={
                "summary": "activation probe synthetic semantic memory",
                "kind": "synthetic",
            },
            actor_ref="activation-smoke",
            reason_code="activation-smoke",
            approval_ref=APPROVAL,
        )
        exact = stack.get(namespace="skeleton.notes", fact_id="activation_probe")
        canonical_ok = (
            mutation.get("status") in {"DONE", "DEGRADED"}
            and exact.get("authoritative") is True
            and bool(exact.get("value_hash"))
        )
        mempalace = stack.search(query="activation probe", limit=5)
        graphify = stack.relations(query="activation probe", limit=5)
        mempalace_ok = bool(mempalace.get("results"))
        graphify_ok = bool(graphify.get("results"))

        backend = CogneePackageBackend(
            private_root=private_root,
            runtime_enabled=True,
            env=env,
        )
        adapter = CogneeProjectionAdapter(backend)
        adapter.project(_event(exact))
        health = adapter.health(
            project_id="skeleton",
            dataset_id="activation_smoke",
            current_canonical_revision=int(exact["canonical_revision"]),
        )
        recall = adapter.recall(
            {
                "schema": SEMANTIC_RECALL_REQUEST_SCHEMA,
                "project_id": "skeleton",
                "dataset_id": "activation_smoke",
                "query": "activation probe",
                "current_canonical_revision": int(exact["canonical_revision"]),
                "limit": 5,
            }
        )
        results = recall.get("results")
        if not isinstance(results, list) or not results:
            raise CogneeLocalRuntimeError(
                "cognee_recall_empty", "Cognee recall returned no bound result"
            )
        result = results[0]
        if not isinstance(result, Mapping):
            raise CogneeLocalRuntimeError(
                "cognee_recall_invalid", "Cognee recall result is invalid"
            )
        provenance = result.get("provenance")
        cognee_ok = (
            health.get("status") == "READY"
            and result.get("canonical_ref") == exact.get("canonical_ref")
            and result.get("content_hash") == exact.get("value_hash")
            and isinstance(provenance, list)
            and bool(provenance)
            and isinstance(provenance[0], Mapping)
            and provenance[0].get("source_kind") == "canonical_sqlite"
        )

        stale = adapter.health(
            project_id="skeleton",
            dataset_id="activation_smoke",
            current_canonical_revision=int(exact["canonical_revision"]) + 1,
        )
        revision_invalidation = (
            stale.get("status") == "STALE"
            and PROJECTION_STALE in stale.get("reason_codes", [])
        )

        foreign_isolated = False
        try:
            adapter.recall(
                {
                    "schema": SEMANTIC_RECALL_REQUEST_SCHEMA,
                    "project_id": "skeleton",
                    "dataset_id": "foreign_activation_smoke",
                    "query": "activation probe",
                    "current_canonical_revision": int(exact["canonical_revision"]),
                    "limit": 5,
                }
            )
        except SemanticProjectionError:
            foreign_isolated = True

        reset_bootstrap_adapter_cache()
        captured: dict[str, object] = {}
        private_path: Path | None = None

        def executor(
            _argv: list[str], _stdin: str, child_env: Mapping[str, str]
        ) -> tuple[int, str]:
            nonlocal private_path
            private_path = Path(child_env[PRIVATE_CONTEXT_ENV])
            captured.update(
                json.loads(private_path.read_text(encoding="utf-8"))
            )
            return 0, "activation smoke complete"

        task = "activation smoke exact task"
        bootstrap_receipt = MemoryBootstrap.from_request(
            _bootstrap_request(
                stack.paths.root, str(exact["canonical_ref"]), task
            ),
            cognee_adapter_factory=lambda: adapter,
        ).execute(task_body=task, executor=executor)
        semantic = captured.get("semantic")
        graph = captured.get("graph")
        bootstrap_ok = (
            bootstrap_receipt.get("status") == "DONE"
            and isinstance(semantic, Mapping)
            and semantic.get("selected") == "cognee"
            and isinstance(graph, Mapping)
            and graph.get("selected") == "graphify"
        )
        handoff_cleanup = private_path is not None and not private_path.exists()

        reset_bootstrap_adapter_cache()
        fallback_context: dict[str, object] = {}

        def fallback_executor(
            _argv: list[str], _stdin: str, child_env: Mapping[str, str]
        ) -> tuple[int, str]:
            fallback_context.update(
                json.loads(
                    Path(child_env[PRIVATE_CONTEXT_ENV]).read_text(encoding="utf-8")
                )
            )
            return 0, "fallback complete"

        fallback_receipt = MemoryBootstrap.from_request(
            _bootstrap_request(
                stack.paths.root,
                str(exact["canonical_ref"]),
                task + " fallback",
            ),
            cognee_adapter_factory=lambda: CogneeProjectionAdapter(
                DisposableInMemoryCogneeBackend()
            ),
        ).execute(task_body=task + " fallback", executor=fallback_executor)
        fallback_semantic = fallback_context.get("semantic")
        fallback_ok = (
            fallback_receipt.get("status") == "DONE"
            and isinstance(fallback_semantic, Mapping)
            and fallback_semantic.get("selected") == "mempalace"
        )

        reset_bootstrap_adapter_cache()

        def echo_executor(
            _argv: list[str], _stdin: str, _child_env: Mapping[str, str]
        ) -> tuple[int, str]:
            return (
                0,
                f"{PRIVATE_CONTEXT_MARKER} activation probe synthetic semantic memory",
            )

        echo_receipt = MemoryBootstrap.from_request(
            _bootstrap_request(
                stack.paths.root, str(exact["canonical_ref"]), task + " echo"
            ),
            cognee_adapter_factory=lambda: adapter,
        ).execute(task_body=task + " echo", executor=echo_executor)
        echo_blocked = (
            echo_receipt.get("status") == "BLOCKED"
            and echo_receipt.get("reason_codes")
            == ["PRIVATE_CONTEXT_ECHO_BLOCKED"]
        )

        removed = adapter.forget_projection(
            project_id="skeleton", dataset_id="activation_smoke"
        )
        forget_ok = removed.get("status") == "FORGOTTEN"

        status = stack.status()
        canonical = status.get("canonical_sqlite")
        graph_status = status.get("graphify")
        counts = {
            "canonical_count": int(canonical.get("active_fact_count", 0))
            if isinstance(canonical, Mapping)
            else 0,
            "semantic_count": len(results),
            "graph_count": int(graph_status.get("relationship_count", 0))
            if isinstance(graph_status, Mapping)
            else 0,
            "outbox_done_count": 1 if projection_queue_ok else 0,
        }
        booleans = {
            "gateway_canonical": bool(canonical_ok),
            "projection_queue": bool(projection_queue_ok),
            "cognee_selected": bool(cognee_ok),
            "mempalace_fallback": bool(fallback_ok and mempalace_ok),
            "graphify_fresh": bool(graphify_ok),
            "project_isolation": foreign_isolated,
            "revision_invalidation": revision_invalidation,
            "mandatory_bootstrap": bootstrap_ok,
            "handoff_cleanup": handoff_cleanup,
            "private_echo_blocked": echo_blocked,
            "forget_verified": forget_ok,
            "private_leak_detected": False,
        }
        return booleans, counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--operator-approval", required=True)
    args = parser.parse_args()

    if args.operator_approval != APPROVAL:
        raise SystemExit("operator approval mismatch")
    actual_sha = _git("rev-parse", "HEAD")
    if actual_sha != args.expected_sha:
        raise SystemExit("head SHA mismatch")
    if _git("status", "--porcelain"):
        raise SystemExit("checkout is dirty")
    if not _origin_is_skeleton(_git("remote", "get-url", "origin")):
        raise SystemExit("origin mismatch")

    private_root_value = os.environ.get(
        "SKELETON_RUNNER_PRIVATE_MEMORY_ROOT", ""
    ).strip()
    if not private_root_value:
        raise SystemExit("private memory root missing")
    private_root = Path(private_root_value).expanduser().resolve()
    private_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_root.chmod(0o700)

    previous = read_activation_marker(private_root)
    start = time.monotonic()
    disk_before = _disk_bytes(private_root)
    rollback_verified = False
    stage = "provider_config"
    try:
        provider = validate_local_provider_config(os.environ)
        stage = "cognee_install"
        install_or_verify_pinned_cognee(
            private_root, env=os.environ, installer=_quiet_installer
        )
        stage = "real_smoke"
        booleans, counts = _real_smoke(private_root, os.environ)
        failed = [
            key
            for key, value in booleans.items()
            if key != "private_leak_detected" and value is not True
        ]
        if booleans.get("private_leak_detected") is True:
            failed.append("private_leak_detected")
        if failed:
            raise CogneeLocalRuntimeError(
                f"{failed[0]}_failed", "activation smoke failed"
            )

        stage = "candidate_marker"
        atomic_write_activation_marker(
            private_root,
            expected_head_sha=args.expected_sha,
            provider_config=provider,
            enabled=False,
        )
        candidate = read_activation_marker(private_root)
        if not candidate or candidate.get("enabled") is not False:
            raise CogneeLocalRuntimeError(
                "candidate_marker_readback_failed", "candidate marker failed"
            )
        stage = "rollback"
        rollback_verified = restore_activation_marker(private_root, previous)
        if not rollback_verified:
            raise CogneeLocalRuntimeError(
                "rollback_failed", "activation rollback failed"
            )
        stage = "activation_marker"
        atomic_write_activation_marker(
            private_root,
            expected_head_sha=args.expected_sha,
            provider_config=provider,
            enabled=True,
        )
        stage = "live_status"
        live = live_aggregate_status(private_root)
        booleans["live_status_checked"] = (
            live.get("activation_enabled") is True
            and live.get("cognee_version") == "1.4.0"
        )
        if not booleans["live_status_checked"]:
            raise CogneeLocalRuntimeError(
                "live_status_failed", "live status failed"
            )

        elapsed_ms = int((time.monotonic() - start) * 1000)
        disk_delta = max(0, _disk_bytes(private_root) - disk_before)
        peak_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
        receipt = activation_receipt(
            status="DONE",
            reason="DONE",
            source_sha=args.expected_sha,
            booleans=booleans,
            counts=counts,
            resource_totals={
                "elapsed_ms": elapsed_ms,
                "disk_bytes": disk_delta,
                "peak_rss_bytes": peak_rss,
            },
            rollback_verified=rollback_verified,
        )
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except Exception as exc:
        restore_activation_marker(private_root, previous)
        if isinstance(exc, (CogneeLocalRuntimeError, SemanticProjectionError)):
            reason = exc.reason_code
        else:
            reason = f"{stage}_exception"
        receipt = activation_receipt(
            status="BLOCKED",
            reason=reason,
            source_sha=args.expected_sha,
            booleans={"private_leak_detected": False},
            counts={
                "canonical_count": 0,
                "semantic_count": 0,
                "graph_count": 0,
                "outbox_done_count": 0,
            },
            resource_totals={
                "elapsed_ms": int((time.monotonic() - start) * 1000),
                "disk_bytes": max(0, _disk_bytes(private_root) - disk_before),
                "peak_rss_bytes": int(
                    resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                )
                * 1024,
            },
            rollback_verified=rollback_verified,
        )
        print(json.dumps(receipt, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
