from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping

from core.private_memory_bundle import prepare_private_memory_import_bundle
from core.private_memory_history import canonical_json, content_hash, current_revision, safe_token


PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA = "skeleton.private_memory_gateway.mutation.v1"
PRIVATE_MEMORY_GATEWAY_MUTATION_RECEIPT_SCHEMA = "skeleton.private_memory_gateway.mutation_receipt.v1"


class MemoryGatewayStorageError(RuntimeError):
    """Raised when the private Memory Gateway storage adapter fails closed."""


class PrivateMemoryGatewayStorage:
    """Internal compatibility adapter for local-private CLI mutations."""

    def __init__(self, stack: object) -> None:
        self.stack = stack
        paths = getattr(stack, "paths", None)
        root = getattr(paths, "root", None)
        db = getattr(paths, "db", None)
        if not isinstance(root, Path) or not isinstance(db, Path):
            raise MemoryGatewayStorageError("private memory stack paths are unavailable")
        self._root = root
        self._canonical_db = db
        self._gateway_db = root / "memory_gateway_mutations.sqlite"

    def execute_mutation(self, payload: Mapping[str, Any]) -> dict[str, object]:
        self._ensure_schema()
        replay = self._replay_import_without_source_if_possible(payload)
        if replay is not None:
            return replay
        request = self._normalize_payload(payload)
        existing = self._get_mutation(request["idempotency_key"])
        if existing is not None:
            if existing["payload_hash"] != request["payload_hash"]:
                raise MemoryGatewayStorageError("idempotency key reused with different mutation payload")
            receipt = self._recover_or_replay(existing, request)
            if receipt is not None:
                return receipt
        else:
            self._record_started(request)

        receipt = self._execute_stack_mutation(request)
        self._record_done(request["idempotency_key"], receipt)
        return receipt

    def _normalize_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if payload.get("schema") != PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA:
            raise MemoryGatewayStorageError("private mutation schema is invalid")
        operation = safe_token(str(payload.get("operation", "")), "operation")
        if operation not in {"put", "delete", "import_bundle"}:
            raise MemoryGatewayStorageError("private mutation operation is not approved")
        project_id = safe_token(str(payload.get("project_id", "skeleton")), "project_id")
        if project_id != "skeleton":
            raise MemoryGatewayStorageError("private mutation project is not authorized")
        expected_revision = payload.get("expected_revision")
        if expected_revision is not None and (not isinstance(expected_revision, int) or expected_revision < 0):
            raise MemoryGatewayStorageError("expected revision must be a non-negative integer")

        request: dict[str, Any] = {
            "schema": PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA,
            "operation": operation,
            "project_id": project_id,
            "expected_revision": expected_revision,
            "actor_ref": safe_token(str(payload.get("actor_ref", "operator")), "actor_ref"),
            "reason_code": safe_token(str(payload.get("reason_code", "operator-memory-mutation")), "reason_code"),
            "approval_ref": safe_token(str(payload.get("approval_ref", "local-operator")), "approval_ref"),
        }
        if operation in {"put", "delete"}:
            fact_namespace = safe_token(str(payload.get("fact_namespace", "")), "namespace")
            fact_id = safe_token(str(payload.get("fact_id", "")), "fact_id")
            request.update(
                {
                    "fact_namespace": fact_namespace,
                    "fact_id": fact_id,
                    "canonical_ref": f"{fact_namespace}:{fact_id}",
                }
            )
            if operation == "put":
                value = payload.get("value")
                value_hash = content_hash(value)
                request.update({"value": value, "source_hash": _safe_hash(payload.get("source_hash") or value_hash)})
            else:
                source_hash = _safe_hash(payload.get("source_hash") or content_hash({"delete": request["canonical_ref"]}))
                request["source_hash"] = source_hash
        else:
            basename = _safe_basename(payload.get("basename"))
            expected_sha256 = str(payload.get("expected_sha256", ""))
            prepared = prepare_private_memory_import_bundle(
                private_root=self._root,
                basename=basename,
                expected_sha256=expected_sha256,
                env=payload.get("env") if isinstance(payload.get("env"), Mapping) else None,
            )
            request.update(
                {
                    "basename": basename,
                    "expected_sha256": expected_sha256,
                    "create_backup": bool(payload.get("create_backup", False)),
                    "bundle_id": prepared.bundle_id,
                    "bundle_hash": prepared.bundle_hash,
                    "file_sha256": prepared.file_sha256,
                    "source_hash": _safe_hash(payload.get("source_hash") or prepared.file_sha256),
                    "record_count": len(prepared.facts),
                }
            )
        idempotency_key = str(payload.get("idempotency_key") or _default_idempotency_key(request))
        request["idempotency_key"] = safe_token(idempotency_key, "idempotency_key")
        request["transaction_ref"] = request["idempotency_key"]
        request["payload_hash"] = content_hash(_payload_fingerprint(request))
        return request

    def _replay_import_without_source_if_possible(self, payload: Mapping[str, Any]) -> dict[str, object] | None:
        if payload.get("schema") != PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA:
            return None
        if payload.get("operation") != "import_bundle" or not isinstance(payload.get("idempotency_key"), str):
            return None
        idempotency_key = safe_token(str(payload["idempotency_key"]), "idempotency_key")
        existing = self._get_mutation(idempotency_key)
        if existing is None:
            return None
        stored = json.loads(str(existing["request_json"]))
        for key in ("operation", "basename", "expected_sha256", "project_id"):
            if key in payload and payload.get(key) != stored.get(key):
                raise MemoryGatewayStorageError("idempotency key reused with different mutation payload")
        if "create_backup" in payload and bool(payload.get("create_backup")) != bool(stored.get("create_backup")):
            raise MemoryGatewayStorageError("idempotency key reused with different mutation payload")
        request = dict(stored)
        request["payload_hash"] = str(existing["payload_hash"])
        receipt = self._recover_or_replay(existing, request)
        if receipt is None:
            raise MemoryGatewayStorageError("private mutation is started but source bundle is unavailable")
        return receipt

    def _execute_stack_mutation(self, request: Mapping[str, Any]) -> dict[str, object]:
        expected_revision = request.get("expected_revision")
        before_revision = self._current_revision()
        if expected_revision is not None and before_revision != expected_revision:
            raise MemoryGatewayStorageError("expected revision does not match current canonical revision")
        operation = request["operation"]
        if operation == "put":
            report = self.stack.put(
                namespace=request["fact_namespace"],
                fact_id=request["fact_id"],
                value=request["value"],
                actor_ref=request["actor_ref"],
                reason_code=request["reason_code"],
                approval_ref=request["approval_ref"],
                transaction_ref=request["transaction_ref"],
            )
        elif operation == "delete":
            report = self.stack.delete(
                namespace=request["fact_namespace"],
                fact_id=request["fact_id"],
                actor_ref=request["actor_ref"],
                reason_code=request["reason_code"],
                approval_ref=request["approval_ref"],
                transaction_ref=request["transaction_ref"],
            )
        else:
            report = self.stack.import_bundle(
                request["basename"],
                expected_sha256=request["expected_sha256"],
                create_backup=bool(request["create_backup"]),
                transaction_ref=request["transaction_ref"],
            )
        return self._receipt(request, report, idempotency_classification=str(report.get("idempotency_classification", "NEW_MUTATION")))

    def _recover_or_replay(self, existing: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, object] | None:
        if existing.get("state") == "DONE" and isinstance(existing.get("receipt_json"), str):
            receipt = json.loads(str(existing["receipt_json"]))
            receipt["idempotency_classification"] = "DUPLICATE_IDENTICAL"
            return receipt
        event_report = self._canonical_event_report(str(existing["transaction_ref"]), str(request["operation"]))
        if event_report is None:
            return None
        receipt = self._receipt(request, event_report, idempotency_classification="DUPLICATE_IDENTICAL")
        self._record_done(str(request["idempotency_key"]), receipt)
        return receipt

    def _receipt(
        self,
        request: Mapping[str, Any],
        report: Mapping[str, Any],
        *,
        idempotency_classification: str,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": PRIVATE_MEMORY_GATEWAY_MUTATION_RECEIPT_SCHEMA,
            "status": report.get("status", "DONE"),
            "operation": request["operation"],
            "project_id": request["project_id"],
            "idempotency_key": request["idempotency_key"],
            "idempotency_classification": idempotency_classification,
            "expected_revision": request.get("expected_revision"),
            "canonical_revision": report.get("canonical_revision"),
            "canonical_sqlite": report.get("canonical_sqlite", "DONE"),
            "canonical_ref": report.get("canonical_ref", request.get("canonical_ref")),
            "source_hash": request.get("source_hash"),
            "actor_ref": request.get("actor_ref"),
            "reason_code": request.get("reason_code"),
            "approval_ref": request.get("approval_ref"),
            "indexes": report.get("indexes"),
            "degraded_indexes": report.get("degraded_indexes", []),
        }
        for key in ("bundle_id", "bundle_hash", "file_sha256", "record_count"):
            if key in request:
                payload[key] = request[key]
        if "imported_canonical_refs" in report:
            payload["imported_canonical_refs"] = report["imported_canonical_refs"]
        if "index_rebuild_error_class" in report:
            payload["error_class"] = report["index_rebuild_error_class"]
        canonical_json(payload)
        return payload

    def _canonical_event_report(self, transaction_ref: str, operation: str) -> dict[str, object] | None:
        if not self._canonical_db.is_file():
            return None
        with closing(sqlite3.connect(f"file:{self._canonical_db.as_posix()}?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT event_type, namespace, fact_id, canonical_revision
                FROM private_memory_events
                WHERE transaction_ref = ?
                ORDER BY canonical_revision
                """,
                (transaction_ref,),
            ).fetchall()
        if not rows:
            return None
        last = rows[-1]
        report: dict[str, object] = {
            "status": "DONE",
            "canonical_sqlite": "DONE",
            "canonical_revision": int(last["canonical_revision"]),
            "canonical_ref": f"{last['namespace']}:{last['fact_id']}",
        }
        if operation == "import_bundle":
            report["imported_canonical_refs"] = [f"{row['namespace']}:{row['fact_id']}" for row in rows]
        return report

    def _current_revision(self) -> int:
        if not self._canonical_db.is_file():
            return 0
        with closing(sqlite3.connect(f"file:{self._canonical_db.as_posix()}?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            return current_revision(connection)

    def _ensure_schema(self) -> None:
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(self._gateway_db))) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_gateway_mutations (
                    idempotency_key TEXT PRIMARY KEY,
                    payload_hash TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    transaction_ref TEXT NOT NULL,
                    state TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    receipt_json TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at TEXT
                )
                """
            )
            connection.commit()
        self._gateway_db.chmod(0o600)

    def _get_mutation(self, idempotency_key: str) -> dict[str, object] | None:
        with closing(sqlite3.connect(str(self._gateway_db))) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT idempotency_key, payload_hash, operation, transaction_ref, state, request_json, receipt_json
                FROM memory_gateway_mutations
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
        return dict(row) if row is not None else None

    def _record_started(self, request: Mapping[str, Any]) -> None:
        with closing(sqlite3.connect(str(self._gateway_db))) as connection:
            connection.execute(
                """
                INSERT INTO memory_gateway_mutations (
                    idempotency_key, payload_hash, operation, transaction_ref, state, request_json
                )
                VALUES (?, ?, ?, ?, 'STARTED', ?)
                """,
                (
                    request["idempotency_key"],
                    request["payload_hash"],
                    request["operation"],
                    request["transaction_ref"],
                    canonical_json(_payload_fingerprint(request)),
                ),
            )
            connection.commit()

    def _record_done(self, idempotency_key: str, receipt: Mapping[str, Any]) -> None:
        with closing(sqlite3.connect(str(self._gateway_db))) as connection:
            connection.execute(
                """
                UPDATE memory_gateway_mutations
                SET state = 'DONE', receipt_json = ?, completed_at = CURRENT_TIMESTAMP
                WHERE idempotency_key = ?
                """,
                (canonical_json(receipt), idempotency_key),
            )
            connection.commit()


def _payload_fingerprint(request: Mapping[str, Any]) -> dict[str, object]:
    return {
        key: request[key]
        for key in sorted(request)
        if key not in {"payload_hash", "value"} and key != "expected_revision"
    }


def _default_idempotency_key(request: Mapping[str, Any]) -> str:
    return "cli_" + content_hash(_payload_fingerprint(request))[:48]


_SAFE_BASENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _safe_basename(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_BASENAME_RE.fullmatch(value):
        raise MemoryGatewayStorageError("bundle name must be a safe basename")
    if "/" in value or "\\" in value or value in {".", ".."} or ".." in value:
        raise MemoryGatewayStorageError("bundle name must not traverse")
    return value


def _safe_hash(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Fa-f0-9]{64}", value):
        raise MemoryGatewayStorageError("source hash must be sha256 hex")
    return value.lower()
