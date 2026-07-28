from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping

from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MemoryGateway, capability_token
from core.memory_gateway_storage import (
    PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA,
    PrivateMemoryGatewayStorage,
)
from core.private_memory_history import content_hash
from core.private_memory_stack import PrivateMemoryStack


HOME_EDGE_AUDIT_PERSIST_OPERATION_ID = "home_edge_audit_persist_v1"
HOME_EDGE_AUDIT_PERSIST_RECEIPT_SCHEMA = "skeleton.home_edge.runtime_audit.persist_receipt.v1"


def persist_home_edge_runtime_audit(
    runtime_audit: Mapping[str, Any],
    *,
    private_root: str | Path | None = None,
    inject_sqlite_failure: bool = False,
) -> dict[str, object]:
    audit_id = _audit_id(runtime_audit)
    stack = PrivateMemoryStack(private_root)
    stack.init()
    storage = PrivateMemoryGatewayStorage(stack)
    gateway = MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=False),
        private_memory_storage=storage,
    )
    response = gateway.execute(
        {
            "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
            "namespace": "skeleton",
            "command": "skeleton.memory.private_mutate",
            "payload": {
                "schema": PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA,
                "operation": "home_edge_audit_persist",
                "project_id": "skeleton",
                "dataset_id": "home_edge_01_runtime_audits",
                "audit_id": audit_id,
                "device_id": "home_edge_01",
                "execution_node": "home-edge-01",
                "audit_kind": "RUNTIME_AUDIT",
                "runtime_audit": dict(runtime_audit),
                "actor_ref": "skeleton-runner",
                "reason_code": "home-edge-runtime-audit-persist",
                "approval_ref": HOME_EDGE_AUDIT_PERSIST_OPERATION_ID,
                "idempotency_key": audit_id,
                "inject_sqlite_failure": inject_sqlite_failure,
            },
        }
    )
    payload = response["payload"]
    if not isinstance(payload, Mapping):
        raise RuntimeError("home edge audit gateway receipt malformed")
    verification = verify_home_edge_runtime_audit(
        audit_id,
        payload_hash=content_hash(runtime_audit),
        private_root=private_root,
    )
    return {
        "schema": HOME_EDGE_AUDIT_PERSIST_RECEIPT_SCHEMA,
        "status": "DONE",
        "operation_id": HOME_EDGE_AUDIT_PERSIST_OPERATION_ID,
        "audit_id": audit_id,
        "device_id": "home_edge_01",
        "execution_node": "home-edge-01",
        "payload_hash": content_hash(runtime_audit),
        "canonical_ref": payload.get("canonical_ref"),
        "canonical_revision": payload.get("canonical_revision"),
        "idempotency_classification": payload.get("idempotency_classification"),
        "verification": verification,
    }


def verify_home_edge_runtime_audit(
    audit_id: str,
    *,
    payload_hash: str,
    private_root: str | Path | None = None,
) -> dict[str, object]:
    stack = PrivateMemoryStack(private_root)
    exact = stack.get(namespace="skeleton.home_edge.runtime_audits", fact_id=audit_id)
    value = exact["value"]
    if not isinstance(value, Mapping) or value.get("runtime_audit_payload_hash") != payload_hash:
        raise RuntimeError("home edge audit canonical record verification failed")

    gateway_db = stack.paths.root / "memory_gateway_mutations.sqlite"
    with closing(sqlite3.connect(str(gateway_db))) as connection:
        metadata_count = _count(
            connection,
            "SELECT COUNT(*) FROM home_edge_runtime_audit_metadata WHERE audit_id = ? AND payload_hash = ? AND state = 'DONE'",
            (audit_id, payload_hash),
        )
        state_count = _count(
            connection,
            "SELECT COUNT(*) FROM home_edge_runtime_audit_state WHERE device_id = 'home_edge_01' AND current_audit_id = ? AND payload_hash = ?",
            (audit_id, payload_hash),
        )
        history_count = _count(
            connection,
            "SELECT COUNT(*) FROM home_edge_runtime_audit_state_history WHERE audit_id = ? AND payload_hash = ?",
            (audit_id, payload_hash),
        )
    with closing(sqlite3.connect(str(stack.paths.db))) as connection:
        semantic_count = _count(
            connection,
            "SELECT COUNT(*) FROM private_memory_events WHERE transaction_ref = ?",
            (audit_id,),
        )
    if (metadata_count, state_count, history_count, semantic_count) != (1, 1, 1, 1):
        raise RuntimeError("home edge audit sqlite verification failed")
    return {
        "canonical_record_count": semantic_count,
        "sqlite_metadata_count": metadata_count,
        "sqlite_state_count": state_count,
        "sqlite_history_count": history_count,
    }


def _audit_id(runtime_audit: Mapping[str, Any]) -> str:
    value = runtime_audit.get("audit_id")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("RUNTIME_AUDIT payload requires audit_id")
    return value.strip()


def _count(connection: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> int:
    return int(connection.execute(sql, params).fetchone()[0])
