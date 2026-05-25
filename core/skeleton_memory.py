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
