from __future__ import annotations

import json
import math
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final


RUNNER_LEASE_SCHEMA_VERSION: Final = "1"
ACTIVE_STATUSES: Final = frozenset({"leased", "running"})
TERMINAL_STATUSES: Final = frozenset({"completed", "failed", "abandoned"})
ALL_STATUSES: Final = ACTIVE_STATUSES | TERMINAL_STATUSES
MAX_LEASE_SECONDS: Final = 7 * 24 * 60 * 60
MAX_CHECKPOINT_BYTES: Final = 32 * 1024


class RunnerLeaseStoreError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class RunnerLeaseRecord:
    idempotency_key: str
    task_reference: str
    repo: str
    branch: str
    base_sha: str
    attempt: int
    status: str
    lease_token: str
    acquired_at: float
    heartbeat_at: float
    expires_at: float
    pid: int | None
    checkpoint: Mapping[str, Any] | None
    receipt_hash: str | None
    terminal_reason: str | None
    updated_at: float


class RunnerLeaseStore:
    """Local execution/recovery ledger. It is not a task queue."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
        busy_timeout_ms: int = 5_000,
    ) -> None:
        if not callable(clock) or not callable(token_factory):
            raise TypeError("clock and token_factory must be callable")
        if not isinstance(busy_timeout_ms, int) or busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be a non-negative integer")

        self._clock = clock
        self._token_factory = token_factory
        self._lock = threading.RLock()
        self._closed = False

        path = str(db_path)
        if path != ":memory:":
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            path,
            isolation_level=None,
            check_same_thread=False,
            timeout=max(busy_timeout_ms / 1000, 0.001),
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        self._connection.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._initialize_schema()

    def __enter__(self) -> RunnerLeaseStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def acquire(
        self,
        *,
        idempotency_key: str,
        task_reference: str,
        repo: str,
        branch: str,
        base_sha: str,
        lease_seconds: float,
        now: float | None = None,
        lease_token: str | None = None,
    ) -> RunnerLeaseRecord:
        key = _text(idempotency_key, "idempotency_key")
        reference = _text(task_reference, "task_reference")
        normalized_repo = _text(repo, "repo")
        normalized_branch = _text(branch, "branch")
        normalized_sha = _sha(base_sha, "base_sha")
        duration = _lease_seconds(lease_seconds)
        timestamp = self._timestamp(now)
        token = _text(lease_token or self._token_factory(), "lease_token")

        with self._transaction():
            latest = self._latest_row(key)
            if latest is not None:
                persisted_identity = (
                    latest["task_reference"],
                    latest["repo"],
                    latest["branch"],
                    latest["base_sha"],
                )
                requested_identity = (
                    reference,
                    normalized_repo,
                    normalized_branch,
                    normalized_sha,
                )
                if persisted_identity != requested_identity:
                    raise RunnerLeaseStoreError(
                        "IDEMPOTENCY_METADATA_MISMATCH",
                        "idempotency key belongs to different task metadata",
                    )
                if latest["status"] == "completed":
                    raise RunnerLeaseStoreError(
                        "COMPLETED_REPLAY_BLOCKED",
                        "completed idempotency keys cannot be acquired again",
                    )
                if latest["status"] in ACTIVE_STATUSES:
                    if float(latest["expires_at"]) > timestamp:
                        raise RunnerLeaseStoreError(
                            "LEASE_CONFLICT",
                            "an unexpired lease already exists",
                        )
                    self._connection.execute(
                        """
                        UPDATE runner_leases
                        SET status = 'abandoned', terminal_reason = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        ("lease expired before reacquire", timestamp, latest["id"]),
                    )
                attempt = int(latest["attempt"]) + 1
            else:
                attempt = 1

            try:
                self._connection.execute(
                    """
                    INSERT INTO runner_leases (
                        idempotency_key, task_reference, repo, branch, base_sha,
                        attempt, status, lease_token, acquired_at, heartbeat_at,
                        expires_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'leased', ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        reference,
                        normalized_repo,
                        normalized_branch,
                        normalized_sha,
                        attempt,
                        token,
                        timestamp,
                        timestamp,
                        timestamp + duration,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RunnerLeaseStoreError(
                    "LEASE_CONFLICT",
                    "lease token or attempt already exists",
                ) from exc
            row = self._latest_row(key)
            assert row is not None
            return _record(row)

    def heartbeat(
        self,
        idempotency_key: str,
        lease_token: str,
        lease_seconds: float,
        *,
        now: float | None = None,
    ) -> RunnerLeaseRecord:
        duration = _lease_seconds(lease_seconds)
        timestamp = self._timestamp(now)
        with self._transaction():
            row = self._active_row(idempotency_key, lease_token, timestamp)
            self._connection.execute(
                """
                UPDATE runner_leases
                SET heartbeat_at = ?, expires_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (timestamp, timestamp + duration, timestamp, row["id"]),
            )
            return self._record_by_id(int(row["id"]))

    def set_running_pid(
        self,
        idempotency_key: str,
        lease_token: str,
        pid: int,
        *,
        now: float | None = None,
    ) -> RunnerLeaseRecord:
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise RunnerLeaseStoreError("INVALID_PID", "pid must be a positive integer")
        timestamp = self._timestamp(now)
        with self._transaction():
            row = self._active_row(idempotency_key, lease_token, timestamp)
            self._connection.execute(
                """
                UPDATE runner_leases
                SET status = 'running', pid = ?, updated_at = ?
                WHERE id = ?
                """,
                (pid, timestamp, row["id"]),
            )
            return self._record_by_id(int(row["id"]))

    def save_checkpoint(
        self,
        idempotency_key: str,
        lease_token: str,
        checkpoint: Mapping[str, Any],
        *,
        now: float | None = None,
    ) -> RunnerLeaseRecord:
        checkpoint_json = _checkpoint_json(checkpoint)
        timestamp = self._timestamp(now)
        with self._transaction():
            row = self._active_row(idempotency_key, lease_token, timestamp)
            self._connection.execute(
                """
                UPDATE runner_leases
                SET checkpoint_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (checkpoint_json, timestamp, row["id"]),
            )
            return self._record_by_id(int(row["id"]))

    def complete(
        self,
        idempotency_key: str,
        lease_token: str,
        receipt_hash: str,
        *,
        now: float | None = None,
    ) -> RunnerLeaseRecord:
        return self._finish(
            idempotency_key,
            lease_token,
            status="completed",
            receipt_hash=_receipt_hash(receipt_hash),
            terminal_reason=None,
            now=now,
        )

    def fail(
        self,
        idempotency_key: str,
        lease_token: str,
        reason: str,
        *,
        now: float | None = None,
    ) -> RunnerLeaseRecord:
        return self._finish(
            idempotency_key,
            lease_token,
            status="failed",
            receipt_hash=None,
            terminal_reason=_text(reason, "reason", max_length=2_048),
            now=now,
        )

    def get_latest(self, idempotency_key: str) -> RunnerLeaseRecord | None:
        key = _text(idempotency_key, "idempotency_key")
        with self._lock:
            self._ensure_open()
            self._verify_schema()
            row = self._latest_row(key)
            return None if row is None else _record(row)

    def list_active(self, *, now: float | None = None) -> tuple[RunnerLeaseRecord, ...]:
        timestamp = self._timestamp(now)
        with self._lock:
            self._ensure_open()
            self._verify_schema()
            rows = self._connection.execute(
                """
                SELECT * FROM runner_leases
                WHERE status IN ('leased', 'running') AND expires_at > ?
                ORDER BY idempotency_key, attempt
                """,
                (timestamp,),
            ).fetchall()
            return tuple(_record(row) for row in rows)

    def reconcile(
        self,
        authoritative_active_keys: Iterable[str],
        *,
        now: float | None = None,
    ) -> tuple[str, ...]:
        active_keys = _key_set(authoritative_active_keys)
        timestamp = self._timestamp(now)
        abandoned: list[str] = []
        with self._transaction():
            rows = self._connection.execute(
                """
                SELECT * FROM runner_leases
                WHERE status IN ('leased', 'running')
                ORDER BY idempotency_key, attempt
                """
            ).fetchall()
            for row in rows:
                key = str(row["idempotency_key"])
                expired = float(row["expires_at"]) <= timestamp
                missing_from_github = key not in active_keys
                if not expired and not missing_from_github:
                    continue
                reason = (
                    "lease expired during reconciliation"
                    if expired
                    else "task no longer active in GitHub snapshot"
                )
                self._connection.execute(
                    """
                    UPDATE runner_leases
                    SET status = 'abandoned', terminal_reason = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (reason, timestamp, row["id"]),
                )
                abandoned.append(key)
        return tuple(sorted(abandoned))

    def _finish(
        self,
        idempotency_key: str,
        lease_token: str,
        *,
        status: str,
        receipt_hash: str | None,
        terminal_reason: str | None,
        now: float | None,
    ) -> RunnerLeaseRecord:
        if status not in {"completed", "failed"}:
            raise ValueError("unsupported terminal status")
        timestamp = self._timestamp(now)
        with self._transaction():
            row = self._active_row(idempotency_key, lease_token, timestamp)
            self._connection.execute(
                """
                UPDATE runner_leases
                SET status = ?, receipt_hash = ?, terminal_reason = ?,
                    expires_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    receipt_hash,
                    terminal_reason,
                    timestamp,
                    timestamp,
                    row["id"],
                ),
            )
            return self._record_by_id(int(row["id"]))

    def _active_row(
        self,
        idempotency_key: str,
        lease_token: str,
        now: float,
    ) -> sqlite3.Row:
        key = _text(idempotency_key, "idempotency_key")
        token = _text(lease_token, "lease_token")
        row = self._latest_row(key)
        if row is None or row["status"] not in ACTIVE_STATUSES:
            raise RunnerLeaseStoreError("LEASE_NOT_ACTIVE", "no active lease exists")
        if row["lease_token"] != token:
            raise RunnerLeaseStoreError("LEASE_TOKEN_MISMATCH", "lease token does not match")
        if float(row["expires_at"]) <= now:
            self._connection.execute(
                """
                UPDATE runner_leases
                SET status = 'abandoned', terminal_reason = ?, updated_at = ?
                WHERE id = ?
                """,
                ("lease expired", now, row["id"]),
            )
            raise RunnerLeaseStoreError("LEASE_EXPIRED", "lease has expired")
        return row

    def _record_by_id(self, row_id: int) -> RunnerLeaseRecord:
        row = self._connection.execute(
            "SELECT * FROM runner_leases WHERE id = ?",
            (row_id,),
        ).fetchone()
        assert row is not None
        return _record(row)

    def _latest_row(self, key: str) -> sqlite3.Row | None:
        return self._connection.execute(
            """
            SELECT * FROM runner_leases
            WHERE idempotency_key = ?
            ORDER BY attempt DESC
            LIMIT 1
            """,
            (key,),
        ).fetchone()

    def _timestamp(self, value: float | None) -> float:
        timestamp = self._clock() if value is None else value
        if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
            raise RunnerLeaseStoreError("INVALID_TIMESTAMP", "timestamp must be numeric")
        timestamp = float(timestamp)
        if not math.isfinite(timestamp) or timestamp < 0:
            raise RunnerLeaseStoreError("INVALID_TIMESTAMP", "timestamp must be finite and non-negative")
        return timestamp

    def _initialize_schema(self) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    "CREATE TABLE IF NOT EXISTS runner_lease_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                version_row = self._connection.execute(
                    "SELECT value FROM runner_lease_meta WHERE key = 'schema_version'"
                ).fetchone()
                if version_row is None:
                    self._connection.execute(
                        "INSERT INTO runner_lease_meta (key, value) VALUES ('schema_version', ?)",
                        (RUNNER_LEASE_SCHEMA_VERSION,),
                    )
                elif version_row["value"] != RUNNER_LEASE_SCHEMA_VERSION:
                    raise RunnerLeaseStoreError(
                        "SCHEMA_VERSION_MISMATCH",
                        "unsupported Runner lease schema version",
                    )
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runner_leases (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        idempotency_key TEXT NOT NULL,
                        task_reference TEXT NOT NULL,
                        repo TEXT NOT NULL,
                        branch TEXT NOT NULL,
                        base_sha TEXT NOT NULL,
                        attempt INTEGER NOT NULL CHECK (attempt > 0),
                        status TEXT NOT NULL CHECK (status IN ('leased','running','completed','failed','abandoned')),
                        lease_token TEXT NOT NULL UNIQUE,
                        acquired_at REAL NOT NULL,
                        heartbeat_at REAL NOT NULL,
                        expires_at REAL NOT NULL,
                        pid INTEGER,
                        checkpoint_json TEXT,
                        receipt_hash TEXT,
                        terminal_reason TEXT,
                        updated_at REAL NOT NULL,
                        UNIQUE (idempotency_key, attempt)
                    )
                    """
                )
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS runner_leases_key_idx ON runner_leases (idempotency_key, attempt DESC)"
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def _verify_schema(self) -> None:
        row = self._connection.execute(
            "SELECT value FROM runner_lease_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None or row["value"] != RUNNER_LEASE_SCHEMA_VERSION:
            raise RunnerLeaseStoreError(
                "SCHEMA_VERSION_MISMATCH",
                "unsupported Runner lease schema version",
            )

    def _transaction(self):
        store = self

        class Transaction:
            def __enter__(self) -> None:
                store._lock.acquire()
                try:
                    store._ensure_open()
                    store._connection.execute("BEGIN IMMEDIATE")
                    store._verify_schema()
                except Exception:
                    if store._connection.in_transaction:
                        store._connection.execute("ROLLBACK")
                    store._lock.release()
                    raise

            def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
                try:
                    store._connection.execute("ROLLBACK" if exc_type else "COMMIT")
                finally:
                    store._lock.release()

        return Transaction()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RunnerLeaseStoreError("STORE_CLOSED", "lease store is closed")


def _record(row: sqlite3.Row) -> RunnerLeaseRecord:
    checkpoint = None
    if row["checkpoint_json"] is not None:
        try:
            decoded = json.loads(row["checkpoint_json"])
        except (TypeError, ValueError) as exc:
            raise RunnerLeaseStoreError(
                "CHECKPOINT_CORRUPT",
                "stored checkpoint is not valid JSON",
            ) from exc
        if not isinstance(decoded, dict):
            raise RunnerLeaseStoreError("CHECKPOINT_CORRUPT", "stored checkpoint is not an object")
        checkpoint = decoded
    status = str(row["status"])
    if status not in ALL_STATUSES:
        raise RunnerLeaseStoreError("STATUS_CORRUPT", "stored lease status is invalid")
    return RunnerLeaseRecord(
        idempotency_key=str(row["idempotency_key"]),
        task_reference=str(row["task_reference"]),
        repo=str(row["repo"]),
        branch=str(row["branch"]),
        base_sha=str(row["base_sha"]),
        attempt=int(row["attempt"]),
        status=status,
        lease_token=str(row["lease_token"]),
        acquired_at=float(row["acquired_at"]),
        heartbeat_at=float(row["heartbeat_at"]),
        expires_at=float(row["expires_at"]),
        pid=None if row["pid"] is None else int(row["pid"]),
        checkpoint=checkpoint,
        receipt_hash=None if row["receipt_hash"] is None else str(row["receipt_hash"]),
        terminal_reason=None if row["terminal_reason"] is None else str(row["terminal_reason"]),
        updated_at=float(row["updated_at"]),
    )


def _text(value: object, field: str, *, max_length: int = 512) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise RunnerLeaseStoreError("INVALID_METADATA", f"{field} must be a non-empty trimmed string")
    if len(value) > max_length or any(ord(character) < 32 for character in value):
        raise RunnerLeaseStoreError("INVALID_METADATA", f"{field} is malformed")
    return value


def _sha(value: object, field: str) -> str:
    text = _text(value, field, max_length=40)
    if len(text) != 40 or any(character not in "0123456789abcdefABCDEF" for character in text):
        raise RunnerLeaseStoreError("INVALID_METADATA", f"{field} must be a full Git SHA")
    return text.lower()


def _receipt_hash(value: object) -> str:
    text = _text(value, "receipt_hash", max_length=64)
    if len(text) != 64 or any(character not in "0123456789abcdefABCDEF" for character in text):
        raise RunnerLeaseStoreError("INVALID_RECEIPT_HASH", "receipt_hash must be a SHA-256 hex digest")
    return text.lower()


def _lease_seconds(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise RunnerLeaseStoreError("INVALID_LEASE_DURATION", "lease duration must be numeric")
    duration = float(value)
    if not math.isfinite(duration) or duration <= 0 or duration > MAX_LEASE_SECONDS:
        raise RunnerLeaseStoreError("INVALID_LEASE_DURATION", "lease duration is outside the allowed range")
    return duration


def _checkpoint_json(value: object) -> str:
    if not isinstance(value, Mapping):
        raise RunnerLeaseStoreError("INVALID_CHECKPOINT", "checkpoint must be a mapping")
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise RunnerLeaseStoreError("INVALID_CHECKPOINT", "checkpoint must be JSON serializable") from exc
    if len(encoded.encode("utf-8")) > MAX_CHECKPOINT_BYTES:
        raise RunnerLeaseStoreError("INVALID_CHECKPOINT", "checkpoint is too large")
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise RunnerLeaseStoreError("INVALID_CHECKPOINT", "checkpoint must encode an object")
    return encoded


def _key_set(values: Iterable[str]) -> frozenset[str]:
    if isinstance(values, (str, bytes, bytearray)):
        raise RunnerLeaseStoreError("INVALID_AUTHORITATIVE_SNAPSHOT", "active keys must be an iterable")
    normalized = tuple(_text(value, "authoritative_active_key") for value in values)
    if len(set(normalized)) != len(normalized):
        raise RunnerLeaseStoreError("INVALID_AUTHORITATIVE_SNAPSHOT", "active keys must be unique")
    return frozenset(normalized)
