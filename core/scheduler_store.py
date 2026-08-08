from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator

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
    "running": frozenset({"done", "failed", "needs_operator"}),
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
                    UNIQUE(schedule_id, schedule_version, scheduled_for),
                    FOREIGN KEY(schedule_id, schedule_version)
                        REFERENCES schedule_versions(schedule_id, version)
                );

                CREATE INDEX IF NOT EXISTS idx_occurrences_schedule_state
                    ON occurrences(schedule_id, state, scheduled_for);
                CREATE INDEX IF NOT EXISTS idx_occurrences_updated
                    ON occurrences(state, updated_at);

                CREATE TABLE IF NOT EXISTS notification_ledger (
                    notification_key TEXT PRIMARY KEY,
                    issue_number INTEGER NOT NULL CHECK(issue_number > 0),
                    status TEXT NOT NULL,
                    report_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL CHECK(created_at >= 0),
                    last_seen_at INTEGER NOT NULL CHECK(last_seen_at >= 0),
                    seen_count INTEGER NOT NULL CHECK(seen_count >= 1)
                );

                CREATE INDEX IF NOT EXISTS idx_notification_ledger_created
                    ON notification_ledger(created_at);
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
                    state, reason, proposal_json, created_at, updated_at, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    occurrence_id, schedule.spec.schedule_id, schedule.version,
                    scheduled_for, state, reason, proposal_json, now, now,
                    now if state == "running" else None,
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
                       started_at = CASE WHEN ? IS NOT NULL THEN ? ELSE started_at END
                 WHERE occurrence_id = ? AND state IN ({placeholders})
                """,
                [
                    new_state, reason, now, started_at, started_at, occurrence_id,
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

    def recover_stale_running(self, *, now: int, stale_after_seconds: int) -> int:
        _timestamp(now, "now")
        if stale_after_seconds <= 0:
            raise SchedulerValidationError(
                "INVALID_STALE_AFTER", "stale_after_seconds must be positive"
            )
        cutoff = max(0, now - stale_after_seconds)
        with self._transaction() as connection:
            result = connection.execute(
                """
                UPDATE occurrences
                   SET state = 'needs_operator',
                       reason = 'STALE_RUNNING_RECOVERY',
                       updated_at = ?
                 WHERE state = 'running' AND started_at IS NOT NULL AND started_at <= ?
                """,
                (now, cutoff),
            )
            return result.rowcount

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

    def claim_notification_once(
        self,
        *,
        notification_key: str,
        issue_number: int,
        status: str,
        report_hash: str,
        now: int,
        limit: int,
    ) -> bool:
        _notification_key(notification_key)
        _issue_number(issue_number)
        _notification_status(status)
        _sha256_hex(report_hash, "report_hash")
        _timestamp(now, "now")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise SchedulerValidationError(
                "INVALID_NOTIFICATION_LIMIT", "limit must be a positive integer"
            )
        with self._transaction() as connection:
            result = connection.execute(
                """
                INSERT OR IGNORE INTO notification_ledger (
                    notification_key, issue_number, status, report_hash,
                    created_at, last_seen_at, seen_count
                ) VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (notification_key, issue_number, status, report_hash, now, now),
            )
            claimed = result.rowcount == 1
            if not claimed:
                connection.execute(
                    """
                    UPDATE notification_ledger
                       SET last_seen_at = ?,
                           seen_count = seen_count + 1
                     WHERE notification_key = ?
                    """,
                    (now, notification_key),
                )
            self._prune_notification_ledger(connection, limit)
            return claimed

    def notification_ledger_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM notification_ledger").fetchone()
        assert row is not None
        return int(row[0])

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
        )

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

    @staticmethod
    def _prune_notification_ledger(
        connection: sqlite3.Connection, limit: int
    ) -> None:
        connection.execute(
            """
            DELETE FROM notification_ledger
             WHERE notification_key IN (
                SELECT notification_key
                  FROM notification_ledger
                 ORDER BY created_at DESC, notification_key DESC
                 LIMIT -1 OFFSET ?
             )
            """,
            (limit,),
        )


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


def _notification_key(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 200:
        raise SchedulerValidationError(
            "INVALID_NOTIFICATION_KEY", "notification key is invalid"
        )
    return value


def _issue_number(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SchedulerValidationError(
            "INVALID_ISSUE_NUMBER", "issue number must be a positive integer"
        )
    return value


def _notification_status(value: object) -> str:
    if not isinstance(value, str) or value != "NEEDS_OPERATOR":
        raise SchedulerValidationError(
            "INVALID_NOTIFICATION_STATUS", "notification status is invalid"
        )
    return value


def _sha256_hex(value: object, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise SchedulerValidationError(
            f"INVALID_{field.upper()}", f"{field} must be a sha256 hex digest"
        )
    return value
