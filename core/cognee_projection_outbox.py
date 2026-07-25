from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Mapping, Protocol

from core.cognee_local_runtime import (
    COGNEE_PROJECTION_DOCUMENT_SCHEMA,
    cognee_runtime_paths,
    opaque_dataset_name,
)
from core.private_memory_stack import PrivateMemoryStack, PrivateMemoryStackError
from core.semantic_memory_projection import (
    SEMANTIC_PROJECTION_EVENT_SCHEMA,
    SemanticProjectionEvent,
    SemanticScope,
    projection_text_hash,
    sanitize_projection_event,
)

OUTBOX_DATABASE_NAME = "memory_gateway_mutations.sqlite"
OUTBOX_TABLE = "memory_gateway_projection_outbox"
MAX_DRAIN_ITEMS = 256
MAX_PROJECTION_TEXT_CHARS = 4096


class CogneeProjectionOutboxError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class ProjectionBackend(Protocol):
    def project(self, event: SemanticProjectionEvent) -> None: ...
    def forget_projection(self, scope: SemanticScope) -> int: ...


def drain_projection_outbox(
    private_root: str | Path,
    scope: SemanticScope,
    backend: ProjectionBackend,
    *,
    limit: int = MAX_DRAIN_ITEMS,
) -> dict[str, int]:
    root = Path(private_root).expanduser().resolve()
    database = root / OUTBOX_DATABASE_NAME
    if not database.is_file():
        return {"claimed_count": 0, "projected_count": 0, "forgotten_count": 0}
    bounded_limit = min(max(int(limit), 1), MAX_DRAIN_ITEMS)
    rows = _claim_rows(database, scope, bounded_limit)
    projected = 0
    forgotten = 0
    stack = PrivateMemoryStack(root)
    for row in rows:
        try:
            operation = str(row["operation"])
            revision = int(row["canonical_revision"])
            if operation == "delete":
                backend.forget_projection(scope)
                forgotten += 1
                for canonical_ref in _active_scope_refs(database, scope):
                    event = _event_from_active_ref(stack, scope, canonical_ref)
                    if event is not None:
                        backend.project(event)
                        projected += 1
            else:
                event = _event_from_active_ref(stack, scope, str(row["canonical_ref"]))
                if event is not None:
                    backend.project(event)
                    projected += 1
            _mark_dataset_revision(root, scope, revision)
            _finish_row(database, str(row["work_key"]), "DONE")
        except Exception as exc:
            _finish_row(database, str(row["work_key"]), "QUEUED")
            if isinstance(exc, CogneeProjectionOutboxError):
                raise
            raise CogneeProjectionOutboxError(
                "projection_outbox_processing_failed",
                "durable Cognee projection work failed",
            ) from exc
    return {
        "claimed_count": len(rows),
        "projected_count": projected,
        "forgotten_count": forgotten,
    }


def projection_outbox_status(
    private_root: str | Path, scope: SemanticScope
) -> dict[str, int]:
    database = Path(private_root).expanduser().resolve() / OUTBOX_DATABASE_NAME
    if not database.is_file():
        return {"queued_count": 0, "processing_count": 0, "done_count": 0}
    with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as connection:
        rows = connection.execute(
            f"""
            SELECT state, COUNT(*)
            FROM {OUTBOX_TABLE}
            WHERE project_id = ? AND dataset_id = ?
            GROUP BY state
            """,
            (scope.project_id, scope.dataset_id),
        ).fetchall()
    counts = {str(state): int(count) for state, count in rows}
    return {
        "queued_count": counts.get("QUEUED", 0),
        "processing_count": counts.get("PROCESSING", 0),
        "done_count": counts.get("DONE", 0),
    }


def _claim_rows(
    database: Path, scope: SemanticScope, limit: int
) -> list[dict[str, object]]:
    try:
        with closing(sqlite3.connect(str(database), timeout=30.0)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                f"""
                SELECT work_key, canonical_ref, canonical_revision, operation
                FROM {OUTBOX_TABLE}
                WHERE project_id = ? AND dataset_id = ?
                  AND state IN ('QUEUED', 'PROCESSING')
                ORDER BY canonical_revision, created_at, work_key
                LIMIT ?
                """,
                (scope.project_id, scope.dataset_id, limit),
            ).fetchall()
            for row in rows:
                connection.execute(
                    f"UPDATE {OUTBOX_TABLE} SET state = 'PROCESSING' WHERE work_key = ?",
                    (str(row["work_key"]),),
                )
            connection.commit()
    except sqlite3.Error as exc:
        raise CogneeProjectionOutboxError(
            "projection_outbox_unavailable", "durable projection outbox is unavailable"
        ) from exc
    return [dict(row) for row in rows]


def _finish_row(database: Path, work_key: str, state: str) -> None:
    if state not in {"DONE", "QUEUED"}:
        raise CogneeProjectionOutboxError(
            "projection_outbox_state_invalid", "projection outbox state is invalid"
        )
    try:
        with closing(sqlite3.connect(str(database), timeout=30.0)) as connection:
            connection.execute(
                f"UPDATE {OUTBOX_TABLE} SET state = ? WHERE work_key = ?",
                (state, work_key),
            )
            connection.commit()
    except sqlite3.Error as exc:
        raise CogneeProjectionOutboxError(
            "projection_outbox_update_failed", "projection outbox update failed"
        ) from exc


def _active_scope_refs(database: Path, scope: SemanticScope) -> tuple[str, ...]:
    with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as connection:
        rows = connection.execute(
            f"""
            SELECT current.canonical_ref
            FROM {OUTBOX_TABLE} AS current
            JOIN (
                SELECT canonical_ref, MAX(canonical_revision) AS max_revision
                FROM {OUTBOX_TABLE}
                WHERE project_id = ? AND dataset_id = ?
                GROUP BY canonical_ref
            ) AS latest
              ON latest.canonical_ref = current.canonical_ref
             AND latest.max_revision = current.canonical_revision
            WHERE current.project_id = ? AND current.dataset_id = ?
              AND current.operation != 'delete'
            ORDER BY current.canonical_ref
            """,
            (scope.project_id, scope.dataset_id, scope.project_id, scope.dataset_id),
        ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _event_from_active_ref(
    stack: PrivateMemoryStack, scope: SemanticScope, canonical_ref: str
) -> SemanticProjectionEvent | None:
    namespace, fact_id = _canonical_ref_parts(canonical_ref)
    try:
        exact = stack.get(namespace=namespace, fact_id=fact_id)
    except PrivateMemoryStackError:
        return None
    text = json.dumps(
        exact["value"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    bounded_text = text[:MAX_PROJECTION_TEXT_CHARS]
    payload = {
        "schema": SEMANTIC_PROJECTION_EVENT_SCHEMA,
        "project_id": scope.project_id,
        "dataset_id": scope.dataset_id,
        "canonical_revision": int(exact["canonical_revision"]),
        "canonical_ref": str(exact["canonical_ref"]),
        "content_hash": str(exact["value_hash"]),
        "projection_text_hash": projection_text_hash(bounded_text),
        "bounded_text": bounded_text,
        "provenance": [
            {
                "schema": COGNEE_PROJECTION_DOCUMENT_SCHEMA,
                "canonical_ref": str(exact["canonical_ref"]),
                "canonical_revision": int(exact["canonical_revision"]),
                "value_hash": str(exact["value_hash"]),
                "source_kind": "canonical_sqlite",
            }
        ],
    }
    return sanitize_projection_event(payload)


def _canonical_ref_parts(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or ":" not in value or "/" in value or "\\" in value:
        raise CogneeProjectionOutboxError(
            "projection_outbox_ref_invalid", "projection canonical ref is invalid"
        )
    namespace, fact_id = value.split(":", 1)
    if not namespace or not fact_id or ".." in namespace or ".." in fact_id:
        raise CogneeProjectionOutboxError(
            "projection_outbox_ref_invalid", "projection canonical ref is invalid"
        )
    return namespace, fact_id


def _mark_dataset_revision(
    private_root: Path, scope: SemanticScope, canonical_revision: int
) -> None:
    paths = cognee_runtime_paths(private_root)
    paths.receipts_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    paths.receipts_dir.chmod(0o700)
    dataset = opaque_dataset_name(scope.project_id, scope.dataset_id)
    receipt_path = paths.receipts_dir / f"{dataset}.json"
    existing: dict[str, object] = {}
    if receipt_path.is_file():
        try:
            loaded = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict):
            existing = dict(loaded)
    existing["schema"] = "skeleton.cognee_projection_receipt_ledger.v1"
    existing["opaque_dataset_hash"] = _sha256(dataset)
    existing["indexed_canonical_revision"] = max(
        int(existing.get("indexed_canonical_revision", 0)), canonical_revision
    )
    existing["event_count"] = int(existing.get("event_count", 0))
    existing["updated_at_epoch"] = int(time.time())
    if not isinstance(existing.get("entries"), dict):
        existing["entries"] = {}
    _atomic_private_json(receipt_path, existing)


def _atomic_private_json(path: Path, payload: Mapping[str, object]) -> None:
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    temporary = Path(name)
    try:
        os.fchmod(fd, 0o600)
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        os.write(fd, encoded)
        os.close(fd)
        fd = -1
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if fd >= 0:
            os.close(fd)
        temporary.unlink(missing_ok=True)


def _sha256(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()
