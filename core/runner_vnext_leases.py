from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import sqlite3
import threading
from typing import Callable, Iterator


class Lane(str, Enum):
    CODEGEN = "codegen"
    VALIDATE = "validate"
    PUBLISH = "publish"
    MERGE = "merge"
    CONTROL = "control"
    PRIVILEGED = "privileged"


class LeaseError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class LaneLease:
    task_id: str
    lane: Lane
    owner: str
    fence_token: int
    acquired_at: float
    heartbeat_at: float
    ttl_seconds: float
    target_state_ref: str | None
    scope_key: str

    def expired(self, now: float) -> bool:
        return now >= self.heartbeat_at + self.ttl_seconds


@dataclass(frozen=True)
class LeaseReceipt:
    status: str
    reason_code: str
    task_id: str
    lane: Lane
    scope_key: str
    fence_token: int


class LaneLeaseStore:
    """SQLite-backed authoritative vNext lane ownership store.

    GitHub labels and process liveness are projections only. SQLite persistence makes
    lease/fence state recoverable across worker/process restarts.
    """

    def __init__(self, db_path: str | Path = ":memory:", *, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        path = str(db_path)
        if path != ":memory:":
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None, timeout=5.0, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout = 5000")
        if path != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
            self._db.execute("PRAGMA synchronous = FULL")
        self._initialize()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _initialize(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS runner_vnext_lane_fences (
                lane TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                fence_token INTEGER NOT NULL,
                PRIMARY KEY (lane, scope_key)
            );
            CREATE TABLE IF NOT EXISTS runner_vnext_lane_leases (
                lane TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                task_id TEXT NOT NULL,
                owner TEXT NOT NULL,
                fence_token INTEGER NOT NULL,
                acquired_at REAL NOT NULL,
                heartbeat_at REAL NOT NULL,
                ttl_seconds REAL NOT NULL,
                target_state_ref TEXT,
                PRIMARY KEY (lane, scope_key)
            );
            """
        )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except Exception:
                self._db.execute("ROLLBACK")
                raise
            else:
                self._db.execute("COMMIT")

    @staticmethod
    def _record(row: sqlite3.Row) -> LaneLease:
        return LaneLease(
            task_id=row["task_id"],
            lane=Lane(row["lane"]),
            owner=row["owner"],
            fence_token=int(row["fence_token"]),
            acquired_at=float(row["acquired_at"]),
            heartbeat_at=float(row["heartbeat_at"]),
            ttl_seconds=float(row["ttl_seconds"]),
            target_state_ref=row["target_state_ref"],
            scope_key=row["scope_key"],
        )

    def _row(self, lane: Lane, scope_key: str) -> sqlite3.Row | None:
        return self._db.execute(
            "SELECT * FROM runner_vnext_lane_leases WHERE lane = ? AND scope_key = ?",
            (lane.value, scope_key),
        ).fetchone()

    def _next_fence(self, lane: Lane, scope_key: str) -> int:
        row = self._db.execute(
            "SELECT fence_token FROM runner_vnext_lane_fences WHERE lane = ? AND scope_key = ?",
            (lane.value, scope_key),
        ).fetchone()
        fence = (int(row["fence_token"]) if row else 0) + 1
        self._db.execute(
            "INSERT INTO runner_vnext_lane_fences(lane, scope_key, fence_token) VALUES(?,?,?) "
            "ON CONFLICT(lane, scope_key) DO UPDATE SET fence_token = excluded.fence_token",
            (lane.value, scope_key, fence),
        )
        return fence

    def acquire(self, *, task_id: str, lane: Lane, owner: str, scope_key: str,
                ttl_seconds: float, target_state_ref: str | None) -> LeaseReceipt:
        if ttl_seconds <= 0:
            raise LeaseError("INVALID_LEASE_TTL")
        now = self._clock()
        with self._transaction():
            row = self._row(lane, scope_key)
            current = self._record(row) if row is not None else None
            if current is not None and not current.expired(now):
                if current.task_id == task_id and current.owner == owner:
                    if current.target_state_ref != target_state_ref:
                        raise LeaseError("LEASE_TARGET_STATE_MISMATCH")
                    return LeaseReceipt("ALREADY_HELD", "LEASE_ALREADY_HELD", task_id, lane, scope_key, current.fence_token)
                raise LeaseError("LEASE_CONFLICT_ACTIVE_OWNER")
            fence = self._next_fence(lane, scope_key)
            self._db.execute(
                "INSERT INTO runner_vnext_lane_leases(lane,scope_key,task_id,owner,fence_token,acquired_at,heartbeat_at,ttl_seconds,target_state_ref) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(lane,scope_key) DO UPDATE SET "
                "task_id=excluded.task_id,owner=excluded.owner,fence_token=excluded.fence_token,"
                "acquired_at=excluded.acquired_at,heartbeat_at=excluded.heartbeat_at,"
                "ttl_seconds=excluded.ttl_seconds,target_state_ref=excluded.target_state_ref",
                (lane.value, scope_key, task_id, owner, fence, now, now, ttl_seconds, target_state_ref),
            )
            reason = "STALE_LEASE_RECLAIMED" if current is not None else "LEASE_ACQUIRED"
            return LeaseReceipt("ACQUIRED", reason, task_id, lane, scope_key, fence)

    def heartbeat(self, *, lane: Lane, scope_key: str, owner: str, fence_token: int) -> LeaseReceipt:
        now = self._clock()
        with self._transaction():
            row = self._row(lane, scope_key)
            if row is None:
                raise LeaseError("LEASE_NOT_FOUND")
            current = self._record(row)
            if current.fence_token != fence_token:
                raise LeaseError("STALE_FENCE_TOKEN")
            if current.owner != owner:
                raise LeaseError("LEASE_OWNER_MISMATCH")
            if current.expired(now):
                raise LeaseError("LEASE_EXPIRED")
            self._db.execute(
                "UPDATE runner_vnext_lane_leases SET heartbeat_at = ? WHERE lane = ? AND scope_key = ?",
                (now, lane.value, scope_key),
            )
            return LeaseReceipt("HEARTBEAT", "LEASE_HEARTBEAT_RECORDED", current.task_id, lane, scope_key, fence_token)

    def release(self, *, lane: Lane, scope_key: str, owner: str, fence_token: int) -> LeaseReceipt:
        now = self._clock()
        with self._transaction():
            row = self._row(lane, scope_key)
            if row is None:
                raise LeaseError("LEASE_NOT_FOUND")
            current = self._record(row)
            if current.fence_token != fence_token:
                raise LeaseError("STALE_FENCE_TOKEN")
            if current.owner != owner:
                raise LeaseError("LEASE_OWNER_MISMATCH")
            if current.expired(now):
                raise LeaseError("LEASE_EXPIRED")
            self._db.execute(
                "DELETE FROM runner_vnext_lane_leases WHERE lane = ? AND scope_key = ?",
                (lane.value, scope_key),
            )
            return LeaseReceipt("RELEASED", "LEASE_RELEASED", current.task_id, lane, scope_key, fence_token)

    def current(self, *, lane: Lane, scope_key: str) -> LaneLease | None:
        row = self._row(lane, scope_key)
        if row is None:
            return None
        current = self._record(row)
        return None if current.expired(self._clock()) else current
