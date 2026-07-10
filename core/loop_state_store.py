from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from core.loop_controller import LoopContext, LoopDecision, LoopEvent, LoopResult, LoopState


SCHEMA_VERSION = 2
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_REASON_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class LoopStateStoreError(RuntimeError):
    """Base error for operational loop state persistence."""


class LoopStateConflictError(LoopStateStoreError):
    """Raised for duplicate runs, stale writers, or previous-state mismatch."""


class LoopStateCorruptionError(LoopStateStoreError):
    """Raised when persisted operational state fails integrity checks."""


@dataclass(frozen=True)
class StoredLoopRun:
    run_id: str
    task_id: str
    version: int
    context: LoopContext
    context_hash: str
    updated_at: int


@dataclass(frozen=True)
class StoredLoopEvent:
    run_id: str
    version: int
    event: LoopEvent
    accepted: bool
    decision: LoopDecision
    reason: str
    previous: LoopContext
    current: LoopContext
    previous_hash: str
    current_hash: str
    recorded_at: int


class LoopStateStore:
    """Dedicated SQLite authority for public-safe operational loop state."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in {0, 1, SCHEMA_VERSION}:
                raise LoopStateCorruptionError("unsupported loop state schema version")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS loop_runs (
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version >= 0),
                    context_json TEXT NOT NULL,
                    context_hash TEXT NOT NULL,
                    updated_at INTEGER NOT NULL CHECK(updated_at >= 0)
                ) WITHOUT ROWID;

                CREATE TABLE IF NOT EXISTS loop_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version > 0),
                    event TEXT NOT NULL,
                    accepted INTEGER NOT NULL CHECK(accepted IN (0, 1)),
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    previous_context_json TEXT NOT NULL,
                    previous_context_hash TEXT NOT NULL,
                    current_context_json TEXT NOT NULL,
                    current_context_hash TEXT NOT NULL,
                    recorded_at INTEGER NOT NULL CHECK(recorded_at >= 0),
                    UNIQUE(run_id, version),
                    FOREIGN KEY(run_id) REFERENCES loop_runs(run_id) ON DELETE RESTRICT
                );

                CREATE INDEX IF NOT EXISTS idx_loop_events_run_version
                    ON loop_events(run_id, version);

                CREATE TABLE IF NOT EXISTS loop_recovery_replay (
                    idempotency_key TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version > 0),
                    action TEXT NOT NULL,
                    expected_state TEXT NOT NULL,
                    policy_profile TEXT NOT NULL,
                    approval_reference TEXT NOT NULL,
                    packet_hash TEXT NOT NULL,
                    recorded_at INTEGER NOT NULL CHECK(recorded_at >= 0),
                    FOREIGN KEY(run_id, version) REFERENCES loop_events(run_id, version)
                        ON DELETE RESTRICT
                ) WITHOUT ROWID;
                """
            )
            if version != SCHEMA_VERSION:
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()

    def create_run(
        self,
        *,
        run_id: str,
        task_id: str,
        context: LoopContext,
        recorded_at: int,
    ) -> StoredLoopRun:
        run_id = _safe_token(run_id, "run_id")
        task_id = _safe_token(task_id, "task_id")
        _non_negative_int(recorded_at, "recorded_at")
        context_json, context_hash = _encode_context(context)

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO loop_runs(
                        run_id, task_id, version, context_json, context_hash, updated_at
                    ) VALUES (?, ?, 0, ?, ?, ?)
                    """,
                    (run_id, task_id, context_json, context_hash, recorded_at),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise LoopStateConflictError("loop run already exists") from exc
            row = connection.execute(
                "SELECT * FROM loop_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            stored = _run_from_row(row)
            expected = StoredLoopRun(
                run_id=run_id,
                task_id=task_id,
                version=0,
                context=context,
                context_hash=context_hash,
                updated_at=recorded_at,
            )
            if stored != expected:
                connection.rollback()
                raise LoopStateCorruptionError("loop run create read-back mismatch")
            connection.commit()
            return stored

    def load_run(self, run_id: str) -> StoredLoopRun:
        run_id = _safe_token(run_id, "run_id")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM loop_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise LoopStateStoreError("loop run not found")
        return _run_from_row(row)

    def append_result(
        self,
        *,
        run_id: str,
        expected_version: int,
        result: LoopResult,
        recorded_at: int,
    ) -> StoredLoopRun:
        run_id = _safe_token(run_id, "run_id")
        _non_negative_int(expected_version, "expected_version")
        _non_negative_int(recorded_at, "recorded_at")
        _validate_result(result)
        previous_json, previous_hash = _encode_context(result.previous)
        current_json, current_hash = _encode_context(result.current)

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM loop_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise LoopStateStoreError("loop run not found")
            stored = _run_from_row(row)
            if stored.version != expected_version:
                connection.rollback()
                raise LoopStateConflictError("loop run version conflict")
            if stored.context != result.previous or stored.context_hash != previous_hash:
                connection.rollback()
                raise LoopStateConflictError("loop result previous context mismatch")

            next_version = expected_version + 1
            try:
                connection.execute(
                    """
                    INSERT INTO loop_events(
                        run_id, version, event, accepted, decision, reason,
                        previous_context_json, previous_context_hash,
                        current_context_json, current_context_hash, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        next_version,
                        result.event.value,
                        int(result.accepted),
                        result.decision.value,
                        result.reason,
                        previous_json,
                        previous_hash,
                        current_json,
                        current_hash,
                        recorded_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise LoopStateConflictError("loop event version conflict") from exc

            updated = connection.execute(
                """
                UPDATE loop_runs
                SET version = ?, context_json = ?, context_hash = ?, updated_at = ?
                WHERE run_id = ? AND version = ?
                """,
                (
                    next_version,
                    current_json,
                    current_hash,
                    recorded_at,
                    run_id,
                    expected_version,
                ),
            )
            if updated.rowcount != 1:
                connection.rollback()
                raise LoopStateConflictError("loop run version conflict")

            run_row = connection.execute(
                "SELECT * FROM loop_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            event_row = connection.execute(
                "SELECT * FROM loop_events WHERE run_id = ? AND version = ?",
                (run_id, next_version),
            ).fetchone()
            current_run = _run_from_row(run_row)
            current_event = _event_from_row(event_row)
            if current_run.context != result.current or current_run.context_hash != current_hash:
                connection.rollback()
                raise LoopStateCorruptionError("loop run update read-back mismatch")
            if (
                current_event.previous != result.previous
                or current_event.current != result.current
                or current_event.event is not result.event
                or current_event.decision is not result.decision
                or current_event.accepted is not result.accepted
                or current_event.reason != result.reason
            ):
                connection.rollback()
                raise LoopStateCorruptionError("loop event read-back mismatch")
            connection.commit()
            return current_run

    def append_recovery_result(
        self,
        *,
        run_id: str,
        expected_version: int,
        result: LoopResult,
        recorded_at: int,
        idempotency_key: str,
        action: str,
        expected_state: str,
        policy_profile: str,
        approval_reference: str,
        packet_hash: str,
    ) -> StoredLoopRun:
        run_id = _safe_token(run_id, "run_id")
        idempotency_key = _safe_token(idempotency_key, "idempotency_key")
        action = _safe_token(action, "action")
        expected_state = _safe_token(expected_state, "expected_state")
        policy_profile = _safe_token(policy_profile, "policy_profile")
        approval_reference = _safe_token(approval_reference, "approval_reference")
        if not isinstance(packet_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", packet_hash):
            raise ValueError("packet_hash must be a SHA-256 hex digest")
        _non_negative_int(expected_version, "expected_version")
        _non_negative_int(recorded_at, "recorded_at")
        _validate_result(result)
        if result.accepted is not True:
            raise ValueError("recovery replay claim requires an accepted transition")
        if result.previous.state.value != expected_state:
            raise LoopStateConflictError("loop recovery expected state mismatch")
        previous_json, previous_hash = _encode_context(result.previous)
        current_json, current_hash = _encode_context(result.current)

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay_row = connection.execute(
                """
                SELECT * FROM loop_recovery_replay
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
            if replay_row is not None:
                connection.rollback()
                raise LoopStateConflictError("loop recovery replay conflict")

            row = connection.execute(
                "SELECT * FROM loop_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise LoopStateStoreError("loop run not found")
            stored = _run_from_row(row)
            if stored.version != expected_version:
                connection.rollback()
                raise LoopStateConflictError("loop run version conflict")
            if stored.context != result.previous or stored.context_hash != previous_hash:
                connection.rollback()
                raise LoopStateConflictError("loop result previous context mismatch")

            next_version = expected_version + 1
            try:
                connection.execute(
                    """
                    INSERT INTO loop_events(
                        run_id, version, event, accepted, decision, reason,
                        previous_context_json, previous_context_hash,
                        current_context_json, current_context_hash, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        next_version,
                        result.event.value,
                        int(result.accepted),
                        result.decision.value,
                        result.reason,
                        previous_json,
                        previous_hash,
                        current_json,
                        current_hash,
                        recorded_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO loop_recovery_replay(
                        idempotency_key, run_id, version, action, expected_state,
                        policy_profile, approval_reference, packet_hash, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        idempotency_key,
                        run_id,
                        next_version,
                        action,
                        expected_state,
                        policy_profile,
                        approval_reference,
                        packet_hash,
                        recorded_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise LoopStateConflictError("loop recovery replay conflict") from exc

            updated = connection.execute(
                """
                UPDATE loop_runs
                SET version = ?, context_json = ?, context_hash = ?, updated_at = ?
                WHERE run_id = ? AND version = ?
                """,
                (
                    next_version,
                    current_json,
                    current_hash,
                    recorded_at,
                    run_id,
                    expected_version,
                ),
            )
            if updated.rowcount != 1:
                connection.rollback()
                raise LoopStateConflictError("loop run version conflict")

            run_row = connection.execute(
                "SELECT * FROM loop_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            event_row = connection.execute(
                "SELECT * FROM loop_events WHERE run_id = ? AND version = ?",
                (run_id, next_version),
            ).fetchone()
            replay_row = connection.execute(
                """
                SELECT * FROM loop_recovery_replay
                WHERE idempotency_key = ? AND run_id = ? AND version = ?
                """,
                (idempotency_key, run_id, next_version),
            ).fetchone()
            current_run = _run_from_row(run_row)
            current_event = _event_from_row(event_row)
            if replay_row is None:
                connection.rollback()
                raise LoopStateCorruptionError("loop recovery replay read-back missing")
            if current_run.context != result.current or current_run.context_hash != current_hash:
                connection.rollback()
                raise LoopStateCorruptionError("loop run update read-back mismatch")
            if (
                current_event.previous != result.previous
                or current_event.current != result.current
                or current_event.event is not result.event
                or current_event.decision is not result.decision
                or current_event.accepted is not True
                or current_event.reason != result.reason
            ):
                connection.rollback()
                raise LoopStateCorruptionError("loop event read-back mismatch")
            connection.commit()
            return current_run

    def has_recovery_replay(self, idempotency_key: str) -> bool:
        idempotency_key = _safe_token(idempotency_key, "idempotency_key")
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT 1 FROM loop_recovery_replay
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
        return row is not None

    def list_events(self, run_id: str) -> list[StoredLoopEvent]:
        run_id = _safe_token(run_id, "run_id")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM loop_events WHERE run_id = ? ORDER BY version",
                (run_id,),
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        return connection


def _run_from_row(row: sqlite3.Row | None) -> StoredLoopRun:
    if row is None:
        raise LoopStateCorruptionError("loop run read-back missing")
    context = _decode_context(str(row["context_json"]), str(row["context_hash"]))
    return StoredLoopRun(
        run_id=_safe_token(str(row["run_id"]), "run_id"),
        task_id=_safe_token(str(row["task_id"]), "task_id"),
        version=_stored_non_negative_int(row["version"], "version"),
        context=context,
        context_hash=str(row["context_hash"]),
        updated_at=_stored_non_negative_int(row["updated_at"], "updated_at"),
    )


def _event_from_row(row: sqlite3.Row | None) -> StoredLoopEvent:
    if row is None:
        raise LoopStateCorruptionError("loop event read-back missing")
    previous_hash = str(row["previous_context_hash"])
    current_hash = str(row["current_context_hash"])
    previous = _decode_context(str(row["previous_context_json"]), previous_hash)
    current = _decode_context(str(row["current_context_json"]), current_hash)
    try:
        event = LoopEvent(str(row["event"]))
        decision = LoopDecision(str(row["decision"]))
    except ValueError as exc:
        raise LoopStateCorruptionError("unsupported stored loop enum") from exc
    accepted_raw = row["accepted"]
    if accepted_raw not in {0, 1}:
        raise LoopStateCorruptionError("invalid stored accepted flag")
    reason = str(row["reason"])
    if not _SAFE_REASON_RE.fullmatch(reason):
        raise LoopStateCorruptionError("invalid stored loop reason")
    return StoredLoopEvent(
        run_id=_safe_token(str(row["run_id"]), "run_id"),
        version=_stored_positive_int(row["version"], "version"),
        event=event,
        accepted=bool(accepted_raw),
        decision=decision,
        reason=reason,
        previous=previous,
        current=current,
        previous_hash=previous_hash,
        current_hash=current_hash,
        recorded_at=_stored_non_negative_int(row["recorded_at"], "recorded_at"),
    )


def _validate_result(result: LoopResult) -> None:
    if not isinstance(result, LoopResult):
        raise TypeError("result must be LoopResult")
    if not isinstance(result.event, LoopEvent):
        raise TypeError("result.event must be LoopEvent")
    if not isinstance(result.decision, LoopDecision):
        raise TypeError("result.decision must be LoopDecision")
    if not isinstance(result.accepted, bool):
        raise TypeError("result.accepted must be bool")
    if not _SAFE_REASON_RE.fullmatch(result.reason):
        raise ValueError("result.reason must be a stable public-safe token")
    _encode_context(result.previous)
    _encode_context(result.current)


def _encode_context(context: LoopContext) -> tuple[str, str]:
    if not isinstance(context, LoopContext):
        raise TypeError("context must be LoopContext")
    payload = {
        "budget_used": context.budget_used,
        "deadline_at": context.deadline_at,
        "iterations": context.iterations,
        "lease_expires_at": context.lease_expires_at,
        "retries": context.retries,
        "state": context.state.value,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _decode_context(encoded: str, expected_hash: str) -> LoopContext:
    actual_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    if actual_hash != expected_hash:
        raise LoopStateCorruptionError("loop context hash mismatch")
    try:
        payload = json.loads(encoded)
        if not isinstance(payload, dict):
            raise TypeError
        if set(payload) != {
            "budget_used",
            "deadline_at",
            "iterations",
            "lease_expires_at",
            "retries",
            "state",
        }:
            raise ValueError
        context = LoopContext(
            state=LoopState(payload["state"]),
            iterations=payload["iterations"],
            retries=payload["retries"],
            budget_used=payload["budget_used"],
            deadline_at=payload["deadline_at"],
            lease_expires_at=payload["lease_expires_at"],
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LoopStateCorruptionError("invalid stored loop context") from exc
    canonical, canonical_hash = _encode_context(context)
    if canonical != encoded or canonical_hash != expected_hash:
        raise LoopStateCorruptionError("non-canonical stored loop context")
    return context


def _safe_token(value: str, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_TOKEN_RE.fullmatch(value):
        raise ValueError(f"{name} must be a bounded public-safe token")
    return value


def _non_negative_int(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _stored_non_negative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LoopStateCorruptionError(f"invalid stored {name}")
    return value


def _stored_positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise LoopStateCorruptionError(f"invalid stored {name}")
    return value
