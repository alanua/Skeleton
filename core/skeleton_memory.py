from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from core.audit_ledger import validate_public_safe_payload


class SkeletonMemory:
    """Small SQLite-backed store for Skeleton operational state."""

    def __init__(self, db_path: str | Path = ":memory:"):
        self.db_path = str(db_path)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self._configure_connection()

    def init_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_events (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                project_id TEXT,
                actor TEXT,
                metadata_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS project_state (
                project_id TEXT PRIMARY KEY,
                updated_at TEXT NOT NULL,
                state_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS executor_runs (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                project_id TEXT,
                executor TEXT,
                status TEXT,
                metadata_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS decision_records (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                project_id TEXT,
                decision TEXT NOT NULL,
                operator TEXT,
                metadata_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS canon_candidates (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                approved_at TEXT,
                status TEXT NOT NULL,
                operator TEXT,
                candidate_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS private_reference_stubs (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                project_id TEXT,
                reference_type TEXT NOT NULL,
                label TEXT,
                metadata_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS canonical_memory_records (
                id TEXT PRIMARY KEY,
                canonical_revision INTEGER NOT NULL UNIQUE,
                created_revision INTEGER NOT NULL,
                imported_at TEXT NOT NULL,
                namespace TEXT NOT NULL,
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                version INTEGER NOT NULL,
                provenance_ref TEXT NOT NULL,
                supersession_json TEXT NOT NULL,
                integrity_hash TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                authoritative INTEGER NOT NULL,
                UNIQUE(namespace, scope, key, version)
            );

            CREATE TABLE IF NOT EXISTS canonical_import_snapshots (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                record_count INTEGER NOT NULL,
                max_canonical_revision INTEGER
            );

            CREATE TABLE IF NOT EXISTS canonical_import_receipts (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                namespace TEXT NOT NULL,
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                version INTEGER NOT NULL,
                canonical_revision INTEGER NOT NULL,
                integrity_hash TEXT NOT NULL,
                idempotency_classification TEXT NOT NULL,
                receipt_json TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def log_executor_run(self, run: dict[str, Any]) -> str:
        payload = _validated_payload(run, "run")
        run_id = str(payload.get("id") or uuid.uuid4())
        created_at = _utc_now()
        project_id = _optional_str(payload.get("project_id"))
        executor = _optional_str(payload.get("executor"))
        status = _optional_str(payload.get("status"))

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO executor_runs (id, created_at, project_id, executor, status, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, created_at, project_id, executor, status, _json_dumps(payload)),
            )
            self._insert_memory_event(
                event_type="executor_run_logged",
                project_id=project_id,
                actor=executor,
                metadata={"executor_run_id": run_id, "status": status},
                created_at=created_at,
            )
        return run_id

    def log_operator_event(self, event: dict[str, Any]) -> str:
        payload = _validated_payload(event, "event")
        event_id = str(payload.get("id") or uuid.uuid4())
        created_at = _utc_now()
        event_type = str(payload.get("event_type") or payload.get("type") or "operator_event")
        project_id = _optional_str(payload.get("project_id"))
        actor = _optional_str(payload.get("operator") or payload.get("actor"))

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO memory_events (id, created_at, event_type, project_id, actor, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (event_id, created_at, event_type, project_id, actor, _json_dumps(payload)),
            )
        return event_id

    def get_project_state(self, project_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT state_json FROM project_state WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["state_json"])

    def update_project_state(self, project_id: str, state: dict[str, Any]) -> None:
        if not project_id:
            raise ValueError("project_id is required.")
        payload = _validated_payload(state, "state")
        now = _utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO project_state (project_id, updated_at, state_json)
                VALUES (?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    state_json = excluded.state_json
                """,
                (project_id, now, _json_dumps(payload)),
            )

    def submit_canon_candidate(self, candidate: dict[str, Any]) -> str:
        payload = _validated_payload(candidate, "candidate")
        candidate_id = str(payload.get("id") or uuid.uuid4())
        created_at = _utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO canon_candidates
                    (id, created_at, approved_at, status, operator, candidate_json)
                VALUES (?, ?, NULL, ?, NULL, ?)
                """,
                (candidate_id, created_at, "pending", _json_dumps(payload)),
            )
        return candidate_id

    def approve_canon_candidate(self, candidate_id: str, operator: str) -> None:
        if not candidate_id:
            raise ValueError("candidate_id is required.")
        if not operator:
            raise ValueError("operator is required.")

        now = _utc_now()
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE canon_candidates
                SET approved_at = ?, status = ?, operator = ?
                WHERE id = ?
                """,
                (now, "approved", operator, candidate_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"canon candidate not found: {candidate_id}")

    def begin_canonical_import_transaction(self) -> None:
        self.connection.execute("BEGIN IMMEDIATE")

    def commit_canonical_import_transaction(self) -> None:
        self.connection.commit()

    def rollback_canonical_import_transaction(self) -> None:
        self.connection.rollback()

    def create_canonical_pre_import_snapshot(self) -> dict[str, object]:
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS record_count, MAX(canonical_revision) AS max_canonical_revision
            FROM canonical_memory_records
            """
        ).fetchone()
        snapshot_id = f"canonical-import-snapshot-{uuid.uuid4()}"
        created_at = _utc_now()
        record_count = int(row["record_count"] or 0)
        max_revision = row["max_canonical_revision"]
        self.connection.execute(
            """
            INSERT INTO canonical_import_snapshots
                (id, created_at, record_count, max_canonical_revision)
            VALUES (?, ?, ?, ?)
            """,
            (snapshot_id, created_at, record_count, max_revision),
        )
        return {
            "id": snapshot_id,
            "status": "created",
            "record_count": record_count,
            "max_canonical_revision": max_revision,
        }

    def lookup_canonical_record(
        self,
        *,
        namespace: str,
        scope: str,
        key: str,
        version: int | None = None,
    ) -> dict[str, object] | None:
        if version is None:
            row = self.connection.execute(
                """
                SELECT * FROM canonical_memory_records
                WHERE namespace = ? AND scope = ? AND key = ?
                ORDER BY version DESC, canonical_revision DESC
                LIMIT 1
                """,
                (namespace, scope, key),
            ).fetchone()
        else:
            row = self.connection.execute(
                """
                SELECT * FROM canonical_memory_records
                WHERE namespace = ? AND scope = ? AND key = ? AND version = ?
                """,
                (namespace, scope, key, version),
            ).fetchone()
        if row is None:
            return None
        return _canonical_record_from_row(row)

    def list_canonical_records_for_key(
        self,
        *,
        namespace: str,
        scope: str,
        key: str,
    ) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT * FROM canonical_memory_records
            WHERE namespace = ? AND scope = ? AND key = ?
            ORDER BY version, canonical_revision
            """,
            (namespace, scope, key),
        ).fetchall()
        return [_canonical_record_from_row(row) for row in rows]

    def next_canonical_revision(self) -> int:
        row = self.connection.execute(
            "SELECT MAX(canonical_revision) AS max_canonical_revision FROM canonical_memory_records"
        ).fetchone()
        return int(row["max_canonical_revision"] or 0) + 1

    def insert_canonical_record(
        self,
        *,
        namespace: str,
        scope: str,
        key: str,
        version: int,
        provenance_ref: str,
        supersession: Mapping[str, Any],
        integrity_hash: str,
        manifest_json: str,
        canonical_revision: int,
    ) -> dict[str, object]:
        record_id = f"canonical-record-{uuid.uuid4()}"
        imported_at = _utc_now()
        self.connection.execute(
            """
            INSERT INTO canonical_memory_records
                (
                    id,
                    canonical_revision,
                    created_revision,
                    imported_at,
                    namespace,
                    scope,
                    key,
                    version,
                    provenance_ref,
                    supersession_json,
                    integrity_hash,
                    manifest_json,
                    authoritative
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                canonical_revision,
                canonical_revision,
                imported_at,
                namespace,
                scope,
                key,
                version,
                provenance_ref,
                _json_dumps(dict(supersession)),
                integrity_hash,
                manifest_json,
                1,
            ),
        )
        inserted = self.lookup_canonical_record(
            namespace=namespace,
            scope=scope,
            key=key,
            version=version,
        )
        if inserted is None:
            raise RuntimeError("canonical record insert did not read back")
        return inserted

    def insert_canonical_import_receipt(self, receipt: Mapping[str, Any]) -> None:
        payload = dict(receipt)
        self.connection.execute(
            """
            INSERT INTO canonical_import_receipts
                (
                    id,
                    created_at,
                    namespace,
                    scope,
                    key,
                    version,
                    canonical_revision,
                    integrity_hash,
                    idempotency_classification,
                    receipt_json
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"canonical-import-receipt-{uuid.uuid4()}",
                _utc_now(),
                str(payload["namespace_token"]),
                str(payload["scope_token"]),
                str(payload["key_token"]),
                int(payload["version"]),
                int(payload["canonical_revision"]),
                str(payload["integrity_hash"]),
                str(payload["idempotency_classification"]),
                _json_dumps(payload),
            ),
        )

    def _configure_connection(self) -> None:
        self.connection.execute("PRAGMA foreign_keys=ON")
        try:
            self.connection.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass

    def _insert_memory_event(
        self,
        *,
        event_type: str,
        project_id: str | None,
        actor: str | None,
        metadata: Mapping[str, Any],
        created_at: str,
    ) -> str:
        event_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO memory_events (id, created_at, event_type, project_id, actor, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_id, created_at, event_type, project_id, actor, _json_dumps(metadata)),
        )
        return event_id


def _validated_payload(payload: Mapping[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError(f"{name} must be a mapping.")
    validate_public_safe_payload(payload)
    return dict(payload)


def _json_dumps(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical_record_from_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "canonical_revision": row["canonical_revision"],
        "created_revision": row["created_revision"],
        "imported_at": row["imported_at"],
        "namespace": row["namespace"],
        "scope": row["scope"],
        "key": row["key"],
        "version": row["version"],
        "provenance_ref": row["provenance_ref"],
        "supersession": json.loads(row["supersession_json"]),
        "integrity_hash": row["integrity_hash"],
        "manifest_json": row["manifest_json"],
        "authoritative": bool(row["authoritative"]),
    }
