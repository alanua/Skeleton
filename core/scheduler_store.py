from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import hashlib
from typing import Any, Iterator, Mapping

from core.scheduler_models import (
    OCCURRENCE_STATES,
    OccurrenceRecord,
    ScheduleSpec,
    SchedulerValidationError,
    StoredSchedule,
    thaw_json,
)


class SchedulerStoreError(RuntimeError):
    pass


_ALLOWED_TRANSITIONS = {
    "pending": frozenset({"running", "skipped", "needs_operator", "failed"}),
    "running": frozenset(
        {"done", "failed", "waiting_dependency", "needs_operator", "pending"}
    ),
    "waiting_dependency": frozenset({"pending", "needs_operator", "failed", "skipped"}),
    "needs_operator": frozenset({"pending", "skipped", "failed", "done"}),
    "done": frozenset(),
    "failed": frozenset(),
    "skipped": frozenset(),
}


class SchedulerStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schedule_versions (
                    schedule_id TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version >= 1),
                    trigger_kind TEXT NOT NULL,
                    cron_expression TEXT,
                    once_at INTEGER,
                    timezone TEXT NOT NULL,
                    route_type TEXT NOT NULL,
                    route_id TEXT NOT NULL,
                    approval_policy TEXT NOT NULL,
                    overlap_policy TEXT NOT NULL,
                    misfire_policy TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL CHECK(created_at >= 0),
                    spec_hash TEXT NOT NULL,
                    PRIMARY KEY(schedule_id, version)
                );

                CREATE TABLE IF NOT EXISTS schedule_heads (
                    schedule_id TEXT PRIMARY KEY,
                    current_version INTEGER NOT NULL CHECK(current_version >= 1),
                    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                    last_evaluated_at INTEGER,
                    FOREIGN KEY(schedule_id, current_version)
                        REFERENCES schedule_versions(schedule_id, version)
                );

                CREATE TABLE IF NOT EXISTS occurrences (
                    occurrence_id TEXT PRIMARY KEY,
                    schedule_id TEXT NOT NULL,
                    schedule_version INTEGER NOT NULL CHECK(schedule_version >= 1),
                    scheduled_for INTEGER NOT NULL CHECK(scheduled_for >= 0),
                    state TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    proposal_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL CHECK(created_at >= 0),
                    updated_at INTEGER NOT NULL CHECK(updated_at >= 0),
                    started_at INTEGER,
                    attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt >= 0),
                    idempotency_key TEXT,
                    parent_occurrence_id TEXT,
                    parent_receipt_id TEXT,
                    claim_owner TEXT,
                    lease_expires_at INTEGER,
                    heartbeat_at INTEGER,
                    UNIQUE(schedule_id, schedule_version, scheduled_for),
                    FOREIGN KEY(schedule_id, schedule_version)
                        REFERENCES schedule_versions(schedule_id, version)
                );

                CREATE TABLE IF NOT EXISTS dispatch_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    occurrence_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL CHECK(attempt >= 1),
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    evidence_ref TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL CHECK(created_at >= 0),
                    parent_receipt_id TEXT,
                    FOREIGN KEY(occurrence_id) REFERENCES occurrences(occurrence_id)
                );

                CREATE INDEX IF NOT EXISTS idx_occurrences_schedule_state
                    ON occurrences(schedule_id, state, scheduled_for);
                CREATE INDEX IF NOT EXISTS idx_occurrences_updated
                    ON occurrences(state, updated_at);
                CREATE INDEX IF NOT EXISTS idx_occurrences_pending
                    ON occurrences(state, scheduled_for, occurrence_id);
                CREATE INDEX IF NOT EXISTS idx_dispatch_receipts_occurrence
                    ON dispatch_receipts(occurrence_id, attempt);
                """
            )
            self._ensure_occurrence_columns(connection)
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_occurrences_running_lease
                    ON occurrences(state, lease_expires_at, started_at)
                """
            )

    def register(
        self,
        spec: ScheduleSpec,
        *,
        now: int,
        enabled: bool = True,
    ) -> tuple[StoredSchedule, bool]:
        _timestamp(now, "now")
        if not isinstance(enabled, bool):
            raise SchedulerValidationError("INVALID_ENABLED", "enabled must be boolean")
        payload_json = json.dumps(
            thaw_json(spec.payload), ensure_ascii=True, allow_nan=False,
            separators=(",", ":"), sort_keys=True,
        )
        spec_hash = spec.deterministic_hash()
        with self._transaction() as connection:
            head = connection.execute(
                """
                SELECT h.current_version, h.enabled, h.last_evaluated_at, v.spec_hash
                FROM schedule_heads h
                JOIN schedule_versions v
                  ON v.schedule_id = h.schedule_id AND v.version = h.current_version
                WHERE h.schedule_id = ?
                """,
                (spec.schedule_id,),
            ).fetchone()
            if head is not None and head[3] == spec_hash:
                record = self._load_current(connection, spec.schedule_id)
                assert record is not None
                return record, False

            version = 1 if head is None else int(head[0]) + 1
            connection.execute(
                """
                INSERT INTO schedule_versions (
                    schedule_id, version, trigger_kind, cron_expression, once_at,
                    timezone, route_type, route_id, approval_policy, overlap_policy,
                    misfire_policy, payload_json, created_at, spec_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    spec.schedule_id, version, spec.trigger_kind, spec.cron_expression,
                    spec.once_at, spec.timezone, spec.route_type, spec.route_id,
                    spec.approval_policy, spec.overlap_policy, spec.misfire_policy,
                    payload_json, now, spec_hash,
                ),
            )
            connection.execute(
                """
                INSERT INTO schedule_heads (
                    schedule_id, current_version, enabled, last_evaluated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(schedule_id) DO UPDATE SET
                    current_version = excluded.current_version,
                    enabled = excluded.enabled,
                    last_evaluated_at = excluded.last_evaluated_at
                """,
                (spec.schedule_id, version, int(enabled), max(0, now - 60)),
            )
            record = self._load_current(connection, spec.schedule_id)
            assert record is not None
            return record, True

    def set_enabled(self, schedule_id: str, enabled: bool) -> StoredSchedule:
        if not isinstance(enabled, bool):
            raise SchedulerValidationError("INVALID_ENABLED", "enabled must be boolean")
        with self._transaction() as connection:
            result = connection.execute(
                "UPDATE schedule_heads SET enabled = ? WHERE schedule_id = ?",
                (int(enabled), schedule_id),
            )
            if result.rowcount != 1:
                raise SchedulerStoreError("SCHEDULE_NOT_FOUND")
            record = self._load_current(connection, schedule_id)
            assert record is not None
            return record

    def get_current(self, schedule_id: str) -> StoredSchedule | None:
        with self._connect() as connection:
            return self._load_current(connection, schedule_id)

    def get_occurrence(self, occurrence_id: str) -> OccurrenceRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM occurrences WHERE occurrence_id = ?", (occurrence_id,)
            ).fetchone()
            return None if row is None else self._occurrence_from_row(row)

    def list_enabled(self) -> tuple[StoredSchedule, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT v.schedule_id, v.version, h.enabled, v.created_at,
                       h.last_evaluated_at, v.trigger_kind, v.cron_expression,
                       v.once_at, v.timezone, v.route_type, v.route_id,
                       v.approval_policy, v.overlap_policy, v.misfire_policy,
                       v.payload_json
                FROM schedule_heads h
                JOIN schedule_versions v
                  ON v.schedule_id = h.schedule_id AND v.version = h.current_version
                WHERE h.enabled = 1
                ORDER BY v.schedule_id
                """
            ).fetchall()
            return tuple(self._schedule_from_row(row) for row in rows)

    def advance_cursor(
        self, schedule_id: str, *, expected_version: int, evaluated_at: int
    ) -> bool:
        _timestamp(evaluated_at, "evaluated_at")
        with self._transaction() as connection:
            result = connection.execute(
                """
                UPDATE schedule_heads
                   SET last_evaluated_at = CASE
                       WHEN last_evaluated_at IS NULL OR last_evaluated_at < ? THEN ?
                       ELSE last_evaluated_at END
                 WHERE schedule_id = ? AND current_version = ?
                """,
                (evaluated_at, evaluated_at, schedule_id, expected_version),
            )
            return result.rowcount == 1

    def create_occurrence(
        self,
        *,
        occurrence_id: str,
        schedule: StoredSchedule,
        scheduled_for: int,
        state: str,
        reason: str,
        proposal: dict[str, Any],
        now: int,
        parent_occurrence_id: str | None = None,
        parent_receipt_id: str | None = None,
    ) -> tuple[OccurrenceRecord, bool]:
        _timestamp(scheduled_for, "scheduled_for")
        _timestamp(now, "now")
        _state(state)
        _reason(reason)
        proposal_json = json.dumps(
            proposal, ensure_ascii=True, allow_nan=False,
            separators=(",", ":"), sort_keys=True,
        )
        with self._transaction() as connection:
            result = connection.execute(
                """
                INSERT OR IGNORE INTO occurrences (
                    occurrence_id, schedule_id, schedule_version, scheduled_for,
                    state, reason, proposal_json, created_at, updated_at, started_at,
                    parent_occurrence_id, parent_receipt_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    occurrence_id, schedule.spec.schedule_id, schedule.version,
                    scheduled_for, state, reason, proposal_json, now, now,
                    now if state == "running" else None, parent_occurrence_id,
                    parent_receipt_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM occurrences WHERE occurrence_id = ?", (occurrence_id,)
            ).fetchone()
            if row is None:
                row = connection.execute(
                    """
                    SELECT * FROM occurrences
                    WHERE schedule_id = ? AND schedule_version = ? AND scheduled_for = ?
                    """,
                    (schedule.spec.schedule_id, schedule.version, scheduled_for),
                ).fetchone()
            if row is None:
                raise SchedulerStoreError("OCCURRENCE_CREATE_FAILED")
            return self._occurrence_from_row(row), result.rowcount == 1

    def claim_next_pending(
        self,
        *,
        now: int,
        owner: str = "scheduler",
        lease_seconds: int = 60 * 60,
        exclude_occurrence_ids: frozenset[str] = frozenset(),
    ) -> OccurrenceRecord | None:
        _timestamp(now, "now")
        _owner(owner)
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds <= 0:
            raise SchedulerValidationError("INVALID_LEASE_SECONDS", "lease_seconds must be positive")
        lease_expires_at = now + lease_seconds
        with self._transaction() as connection:
            where = "state = 'pending'"
            params: list[object] = []
            if exclude_occurrence_ids:
                placeholders = ",".join("?" for _ in exclude_occurrence_ids)
                where += f" AND occurrence_id NOT IN ({placeholders})"
                params.extend(sorted(exclude_occurrence_ids))
            row = connection.execute(
                f"""
                SELECT * FROM occurrences
                 WHERE {where}
                 ORDER BY scheduled_for, occurrence_id
                 LIMIT 1
                """,
                params,
            ).fetchone()
            if row is None:
                return None
            current = self._occurrence_from_row(row)
            next_attempt = current.attempt + 1
            idempotency_key = f"{current.occurrence_id}:attempt:{next_attempt}"
            result = connection.execute(
                """
                UPDATE occurrences
                   SET state = 'running',
                       reason = 'DISPATCH_CLAIMED',
                       updated_at = ?,
                       started_at = ?,
                       attempt = ?,
                       idempotency_key = ?,
                       claim_owner = ?,
                       lease_expires_at = ?,
                       heartbeat_at = ?
                 WHERE occurrence_id = ? AND state = 'pending' AND attempt = ?
                """,
                (
                    now,
                    now,
                    next_attempt,
                    idempotency_key,
                    owner,
                    lease_expires_at,
                    now,
                    current.occurrence_id,
                    current.attempt,
                ),
            )
            if result.rowcount != 1:
                raise SchedulerStoreError("OCCURRENCE_CLAIM_CONFLICT")
            claimed = connection.execute(
                "SELECT * FROM occurrences WHERE occurrence_id = ?",
                (current.occurrence_id,),
            ).fetchone()
            assert claimed is not None
            return self._occurrence_from_row(claimed)

    def renew_running_claim(
        self,
        occurrence_id: str,
        *,
        owner: str,
        lease_seconds: int,
        now: int,
    ) -> bool:
        _timestamp(now, "now")
        _owner(owner)
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds <= 0:
            raise SchedulerValidationError("INVALID_LEASE_SECONDS", "lease_seconds must be positive")
        with self._transaction() as connection:
            result = connection.execute(
                """
                UPDATE occurrences
                   SET updated_at = ?,
                       heartbeat_at = ?,
                       lease_expires_at = ?
                 WHERE occurrence_id = ?
                   AND state = 'running'
                   AND claim_owner = ?
                   AND (lease_expires_at IS NULL OR lease_expires_at >= ?)
                """,
                (now, now, now + lease_seconds, occurrence_id, owner, now),
            )
            return result.rowcount == 1

    def transition_occurrence(
        self,
        occurrence_id: str,
        *,
        expected_states: set[str] | frozenset[str],
        new_state: str,
        reason: str,
        now: int,
    ) -> OccurrenceRecord:
        _state(new_state)
        _reason(reason)
        _timestamp(now, "now")
        if not expected_states:
            raise SchedulerValidationError("INVALID_EXPECTED_STATES", "expected states empty")
        for state in expected_states:
            _state(state)
            if new_state not in _ALLOWED_TRANSITIONS[state]:
                raise SchedulerValidationError(
                    "INVALID_OCCURRENCE_TRANSITION",
                    f"transition {state} -> {new_state} is not allowed",
                )
        placeholders = ",".join("?" for _ in expected_states)
        started_at = now if new_state == "running" else None
        with self._transaction() as connection:
            result = connection.execute(
                f"""
                UPDATE occurrences
                   SET state = ?, reason = ?, updated_at = ?,
                       started_at = CASE WHEN ? IS NOT NULL THEN ? ELSE started_at END,
                       claim_owner = CASE WHEN ? = 'running' THEN claim_owner ELSE NULL END,
                       lease_expires_at = CASE WHEN ? = 'running' THEN lease_expires_at ELSE NULL END,
                       heartbeat_at = CASE WHEN ? = 'running' THEN heartbeat_at ELSE NULL END
                 WHERE occurrence_id = ? AND state IN ({placeholders})
                """,
                [
                    new_state, reason, now, started_at, started_at,
                    new_state, new_state, new_state, occurrence_id,
                    *sorted(expected_states),
                ],
            )
            if result.rowcount != 1:
                raise SchedulerStoreError("OCCURRENCE_STATE_CONFLICT")
            row = connection.execute(
                "SELECT * FROM occurrences WHERE occurrence_id = ?", (occurrence_id,)
            ).fetchone()
            assert row is not None
            return self._occurrence_from_row(row)

    def recover_stale_running(
        self, *, now: int, stale_after_seconds: int, max_attempts: int = 2
    ) -> dict[str, int]:
        _timestamp(now, "now")
        if stale_after_seconds <= 0:
            raise SchedulerValidationError(
                "INVALID_STALE_AFTER", "stale_after_seconds must be positive"
            )
        if max_attempts <= 0:
            raise SchedulerValidationError("INVALID_MAX_ATTEMPTS", "max_attempts must be positive")
        cutoff = max(0, now - stale_after_seconds)
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM occurrences
                 WHERE state = 'running'
                   AND started_at IS NOT NULL
                   AND (
                       (lease_expires_at IS NOT NULL AND lease_expires_at <= ?)
                       OR (lease_expires_at IS NULL AND started_at <= ?)
                   )
                 ORDER BY scheduled_for, occurrence_id
                """,
                (now, cutoff),
            ).fetchall()
            retried = 0
            needs_operator = 0
            finalized = 0
            for row in rows:
                record = self._occurrence_from_row(row)
                receipt = self._latest_receipt_for_attempt(
                    connection, record.occurrence_id, record.attempt
                )
                if receipt is not None:
                    status = str(receipt["status"])
                    result = json.loads(str(receipt["result_json"]))
                    if status == "done":
                        update = connection.execute(
                            """
                            UPDATE occurrences
                               SET state = 'done',
                                   reason = 'DISPATCH_DONE_AFTER_RESTART',
                                   updated_at = ?,
                                   lease_expires_at = NULL
                             WHERE occurrence_id = ? AND state = 'running'
                            """,
                            (now, record.occurrence_id),
                        )
                        finalized += update.rowcount
                        continue
                    if _receipt_is_ambiguous_mutating(result):
                        update = connection.execute(
                            """
                            UPDATE occurrences
                               SET state = 'needs_operator',
                                   reason = 'AMBIGUOUS_MUTATING_RECEIPT',
                                   updated_at = ?,
                                   lease_expires_at = NULL
                             WHERE occurrence_id = ? AND state = 'running'
                            """,
                            (now, record.occurrence_id),
                        )
                        needs_operator += update.rowcount
                        continue
                if record.attempt < max_attempts:
                    update = connection.execute(
                        """
                        UPDATE occurrences
                           SET state = 'pending',
                               reason = 'STALE_RUNNING_RETRY',
                               updated_at = ?,
                               claim_owner = NULL,
                               lease_expires_at = NULL
                         WHERE occurrence_id = ? AND state = 'running'
                        """,
                        (now, record.occurrence_id),
                    )
                    retried += update.rowcount
                else:
                    update = connection.execute(
                        """
                        UPDATE occurrences
                           SET state = 'needs_operator',
                               reason = 'STALE_RUNNING_RECOVERY_EXHAUSTED',
                               updated_at = ?,
                               lease_expires_at = NULL
                         WHERE occurrence_id = ? AND state = 'running'
                        """,
                        (now, record.occurrence_id),
                    )
                    needs_operator += update.rowcount
            result = {"retried": retried, "needs_operator": needs_operator}
            if finalized:
                result["finalized"] = finalized
            return result

    def resume_waiting_dependencies(self, *, now: int) -> int:
        _timestamp(now, "now")
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM occurrences WHERE state = 'waiting_dependency'"
            ).fetchall()
            resumed = 0
            for row in rows:
                record = self._occurrence_from_row(row)
                payload = record.proposal.get("payload")
                dependency = payload.get("wait_for") if isinstance(payload, Mapping) else None
                if not isinstance(dependency, str):
                    continue
                dependency_row = connection.execute(
                    "SELECT state FROM occurrences WHERE occurrence_id = ?",
                    (dependency,),
                ).fetchone()
                if dependency_row is None or str(dependency_row[0]) != "done":
                    continue
                result = connection.execute(
                    """
                    UPDATE occurrences
                       SET state = 'pending', reason = 'DEPENDENCY_SATISFIED', updated_at = ?
                     WHERE occurrence_id = ? AND state = 'waiting_dependency'
                    """,
                    (now, record.occurrence_id),
                )
                resumed += result.rowcount
            return resumed

    def record_dispatch_receipt(
        self,
        *,
        occurrence_id: str,
        attempt: int,
        idempotency_key: str,
        status: str,
        reason: str,
        evidence_ref: str,
        result: Mapping[str, Any],
        now: int,
        parent_receipt_id: str | None = None,
    ) -> str:
        _timestamp(now, "now")
        if attempt <= 0:
            raise SchedulerValidationError("INVALID_ATTEMPT", "attempt must be positive")
        _reason(reason)
        receipt_id = f"receipt_{_sha256_hex(idempotency_key)[:32]}"
        result_json = json.dumps(
            thaw_json(result), ensure_ascii=True, allow_nan=False,
            separators=(",", ":"), sort_keys=True,
        )
        with self._transaction() as connection:
            existing_rows = connection.execute(
                """
                SELECT * FROM dispatch_receipts
                 WHERE receipt_id = ? OR idempotency_key = ?
                 ORDER BY created_at, receipt_id
                """,
                (receipt_id, idempotency_key),
            ).fetchall()
            for row in existing_rows:
                if _dispatch_receipt_matches(
                    row,
                    receipt_id=receipt_id,
                    occurrence_id=occurrence_id,
                    attempt=attempt,
                    idempotency_key=idempotency_key,
                    status=status,
                    reason=reason,
                    evidence_ref=evidence_ref,
                    result_json=result_json,
                    parent_receipt_id=parent_receipt_id,
                ):
                    return receipt_id
                raise SchedulerStoreError("DISPATCH_RECEIPT_CONFLICT")
            connection.execute(
                """
                INSERT INTO dispatch_receipts(
                    receipt_id, occurrence_id, attempt, idempotency_key, status, reason,
                    evidence_ref, result_json, created_at, parent_receipt_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    occurrence_id,
                    attempt,
                    idempotency_key,
                    status,
                    reason,
                    evidence_ref,
                    result_json,
                    now,
                    parent_receipt_id,
                ),
            )
        return receipt_id

    def list_dispatch_receipts(self, occurrence_id: str) -> tuple[dict[str, Any], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM dispatch_receipts
                 WHERE occurrence_id = ?
                 ORDER BY attempt, receipt_id
                """,
                (occurrence_id,),
            ).fetchall()
            return tuple(
                {
                    "receipt_id": str(row["receipt_id"]),
                    "occurrence_id": str(row["occurrence_id"]),
                    "attempt": int(row["attempt"]),
                    "idempotency_key": str(row["idempotency_key"]),
                    "status": str(row["status"]),
                    "reason": str(row["reason"]),
                    "evidence_ref": str(row["evidence_ref"]),
                    "result": json.loads(str(row["result_json"])),
                    "created_at": int(row["created_at"]),
                    "parent_receipt_id": (
                        None if row["parent_receipt_id"] is None else str(row["parent_receipt_id"])
                    ),
                }
                for row in rows
            )

    @staticmethod
    def _latest_receipt_for_attempt(
        connection: sqlite3.Connection, occurrence_id: str, attempt: int
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT * FROM dispatch_receipts
             WHERE occurrence_id = ? AND attempt = ?
             ORDER BY created_at DESC, receipt_id DESC
             LIMIT 1
            """,
            (occurrence_id, attempt),
        ).fetchone()

    def active_counts(self, schedule_id: str) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT state, COUNT(*) FROM occurrences
                 WHERE schedule_id = ? AND state IN ('pending', 'running')
                 GROUP BY state
                """,
                (schedule_id,),
            ).fetchall()
        counts = {"pending": 0, "running": 0}
        counts.update({str(state): int(count) for state, count in rows})
        return counts

    def occurrence_count(self, schedule_id: str | None = None) -> int:
        with self._connect() as connection:
            if schedule_id is None:
                row = connection.execute("SELECT COUNT(*) FROM occurrences").fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) FROM occurrences WHERE schedule_id = ?", (schedule_id,)
                ).fetchone()
            assert row is not None
            return int(row[0])

    def list_occurrences(self, schedule_id: str) -> tuple[OccurrenceRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM occurrences WHERE schedule_id = ?
                ORDER BY scheduled_for, occurrence_id
                """,
                (schedule_id,),
            ).fetchall()
            return tuple(self._occurrence_from_row(row) for row in rows)

    def status_counts(self) -> dict[str, int]:
        with self._connect() as connection:
            schedule_row = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(enabled), 0) FROM schedule_heads"
            ).fetchone()
            rows = connection.execute(
                "SELECT state, COUNT(*) FROM occurrences GROUP BY state"
            ).fetchall()
        assert schedule_row is not None
        result = {
            "schedules": int(schedule_row[0]),
            "enabled": int(schedule_row[1]),
            **{state: 0 for state in sorted(OCCURRENCE_STATES)},
        }
        result.update({str(state): int(count) for state, count in rows})
        return result

    def _load_current(
        self, connection: sqlite3.Connection, schedule_id: str
    ) -> StoredSchedule | None:
        row = connection.execute(
            """
            SELECT v.schedule_id, v.version, h.enabled, v.created_at,
                   h.last_evaluated_at, v.trigger_kind, v.cron_expression,
                   v.once_at, v.timezone, v.route_type, v.route_id,
                   v.approval_policy, v.overlap_policy, v.misfire_policy,
                   v.payload_json
            FROM schedule_heads h
            JOIN schedule_versions v
              ON v.schedule_id = h.schedule_id AND v.version = h.current_version
            WHERE h.schedule_id = ?
            """,
            (schedule_id,),
        ).fetchone()
        return None if row is None else self._schedule_from_row(row)

    @staticmethod
    def _schedule_from_row(row: sqlite3.Row) -> StoredSchedule:
        spec = ScheduleSpec.from_mapping(
            {
                "schema": "skeleton.schedule.v1",
                "schedule_id": row[0],
                "trigger_kind": row[5],
                "cron_expression": row[6],
                "once_at": row[7],
                "timezone": row[8],
                "route_type": row[9],
                "route_id": row[10],
                "approval_policy": row[11],
                "overlap_policy": row[12],
                "misfire_policy": row[13],
                "payload": json.loads(row[14]),
            }
        )
        return StoredSchedule(
            spec=spec,
            version=int(row[1]),
            enabled=bool(row[2]),
            created_at=int(row[3]),
            last_evaluated_at=None if row[4] is None else int(row[4]),
        )

    @staticmethod
    def _occurrence_from_row(row: sqlite3.Row) -> OccurrenceRecord:
        return OccurrenceRecord(
            occurrence_id=str(row[0]),
            schedule_id=str(row[1]),
            schedule_version=int(row[2]),
            scheduled_for=int(row[3]),
            state=str(row[4]),
            reason=str(row[5]),
            proposal=json.loads(row[6]),
            created_at=int(row[7]),
            updated_at=int(row[8]),
            started_at=None if row[9] is None else int(row[9]),
            attempt=int(row["attempt"]) if "attempt" in row.keys() else 0,
            idempotency_key=(
                None
                if "idempotency_key" not in row.keys() or row["idempotency_key"] is None
                else str(row["idempotency_key"])
            ),
            parent_occurrence_id=(
                None
                if "parent_occurrence_id" not in row.keys() or row["parent_occurrence_id"] is None
                else str(row["parent_occurrence_id"])
            ),
            parent_receipt_id=(
                None
                if "parent_receipt_id" not in row.keys() or row["parent_receipt_id"] is None
                else str(row["parent_receipt_id"])
            ),
        )

    @staticmethod
    def _ensure_occurrence_columns(connection: sqlite3.Connection) -> None:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(occurrences)").fetchall()
        }
        migrations = {
            "attempt": "ALTER TABLE occurrences ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt >= 0)",
            "idempotency_key": "ALTER TABLE occurrences ADD COLUMN idempotency_key TEXT",
            "parent_occurrence_id": "ALTER TABLE occurrences ADD COLUMN parent_occurrence_id TEXT",
            "parent_receipt_id": "ALTER TABLE occurrences ADD COLUMN parent_receipt_id TEXT",
            "claim_owner": "ALTER TABLE occurrences ADD COLUMN claim_owner TEXT",
            "lease_expires_at": "ALTER TABLE occurrences ADD COLUMN lease_expires_at INTEGER",
            "heartbeat_at": "ALTER TABLE occurrences ADD COLUMN heartbeat_at INTEGER",
        }
        for column, statement in migrations.items():
            if column not in columns:
                connection.execute(statement)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection


def _timestamp(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SchedulerValidationError(
            f"INVALID_{field.upper()}", f"{field} must be a non-negative integer"
        )
    return value


def _state(value: object) -> str:
    if not isinstance(value, str) or value not in OCCURRENCE_STATES:
        raise SchedulerValidationError("INVALID_OCCURRENCE_STATE", "state is invalid")
    return value


def _reason(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise SchedulerValidationError("INVALID_REASON", "reason is invalid")
    return value


def _owner(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise SchedulerValidationError("INVALID_CLAIM_OWNER", "claim owner is invalid")
    return value


def _receipt_is_ambiguous_mutating(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    status = str(value.get("status") or "").upper()
    reason = str(value.get("reason") or "").upper()
    decision = str(value.get("decision") or "").upper()
    if (
        value.get("external_side_effects_executed") is True
        and (
            status in {"UNKNOWN", "AMBIGUOUS"}
            or decision in {"UNKNOWN", "AMBIGUOUS"}
            or "AMBIGUOUS" in reason
        )
    ):
        return True
    route_receipt = value.get("route_receipt")
    if isinstance(route_receipt, Mapping):
        return _receipt_is_ambiguous_mutating(route_receipt)
    return False


def _dispatch_receipt_matches(
    row: sqlite3.Row,
    *,
    receipt_id: str,
    occurrence_id: str,
    attempt: int,
    idempotency_key: str,
    status: str,
    reason: str,
    evidence_ref: str,
    result_json: str,
    parent_receipt_id: str | None,
) -> bool:
    return (
        str(row["receipt_id"]) == receipt_id
        and str(row["occurrence_id"]) == occurrence_id
        and int(row["attempt"]) == attempt
        and str(row["idempotency_key"]) == idempotency_key
        and str(row["status"]) == status
        and str(row["reason"]) == reason
        and str(row["evidence_ref"]) == evidence_ref
        and str(row["result_json"]) == result_json
        and (
            None if row["parent_receipt_id"] is None else str(row["parent_receipt_id"])
        )
        == parent_receipt_id
    )


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
