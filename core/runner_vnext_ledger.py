from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import threading
from typing import Iterator


class LedgerError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class OperationIdentity:
    operation_id: str
    idempotency_key: str
    target_state_ref: str


@dataclass(frozen=True)
class ExecutionStartEvidence:
    identity: OperationIdentity
    fence_token: int
    execution_grant_hash: str
    reservation_scope_hash: str


@dataclass(frozen=True)
class LedgerEvent:
    sequence: int
    identity: OperationIdentity
    event_type: str
    fence_token: int
    reason_code: str
    touched_resource_refs: tuple[str, ...]
    before_state_ref: str | None
    after_state_ref: str | None
    validation_status: str | None
    external_dedupe_nonce: str | None
    reservation_scope_hash: str | None


class OperationLedger:
    """Append-only durable vNext operation ledger.

    Reserve-before-effect is mandatory. Current status/fence are derived from events;
    process liveness and GitHub labels are not authoritative.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        path = str(db_path)
        if path != ":memory:":
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, isolation_level=None, timeout=5.0, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout=5000")
        if path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
        self._initialize()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _initialize(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS runner_vnext_operation_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                target_state_ref TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK(event_type IN ('RESERVED','FENCE_REBOUND','COMPLETED','FAILED')),
                terminal INTEGER NOT NULL CHECK(terminal IN (0,1)),
                fence_token INTEGER NOT NULL CHECK(fence_token > 0),
                reason_code TEXT NOT NULL,
                touched_resource_refs TEXT NOT NULL,
                before_state_ref TEXT,
                after_state_ref TEXT,
                validation_status TEXT,
                external_dedupe_nonce TEXT,
                reservation_scope_hash TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS runner_vnext_one_reservation
              ON runner_vnext_operation_events(idempotency_key) WHERE event_type='RESERVED';
            CREATE UNIQUE INDEX IF NOT EXISTS runner_vnext_one_terminal
              ON runner_vnext_operation_events(idempotency_key) WHERE terminal=1;
            CREATE TABLE IF NOT EXISTS runner_vnext_operation_starts (
                idempotency_key TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL,
                target_state_ref TEXT NOT NULL,
                fence_token INTEGER NOT NULL CHECK(fence_token > 0),
                execution_grant_hash TEXT NOT NULL,
                reservation_scope_hash TEXT NOT NULL
            );
            """
        )
        columns = {row["name"] for row in self._db.execute("PRAGMA table_info(runner_vnext_operation_events)").fetchall()}
        if "reservation_scope_hash" not in columns:
            self._db.execute("ALTER TABLE runner_vnext_operation_events ADD COLUMN reservation_scope_hash TEXT")

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

    def reserve(self, identity: OperationIdentity, *, fence_token: int, reservation_scope_hash: str, external_dedupe_nonce: str | None = None) -> LedgerEvent:
        _identity(identity)
        _fence(fence_token)
        _scope_hash(reservation_scope_hash)
        _nonce(external_dedupe_nonce)
        with self._transaction():
            existing = self._reservation(identity.idempotency_key)
            if existing is not None:
                event = _event(existing)
                if event.identity != identity:
                    raise LedgerError("IDEMPOTENCY_TARGET_STATE_MISMATCH")
                if event.external_dedupe_nonce != external_dedupe_nonce:
                    raise LedgerError("IDEMPOTENCY_DEDUPE_NONCE_MISMATCH")
                if event.reservation_scope_hash != reservation_scope_hash:
                    raise LedgerError("IDEMPOTENCY_SCOPE_HASH_MISMATCH")
                return event
            self._insert(
                identity=identity,
                event_type="RESERVED",
                terminal=0,
                fence_token=fence_token,
                reason_code="OPERATION_RESERVED",
                touched_resource_refs=(),
                before_state_ref=None,
                after_state_ref=None,
                validation_status=None,
                external_dedupe_nonce=external_dedupe_nonce,
                reservation_scope_hash=reservation_scope_hash,
            )
            return _event(self._reservation(identity.idempotency_key))

    def rebind_fence(self, identity: OperationIdentity, *, expected_fence_token: int, new_fence_token: int) -> LedgerEvent:
        _fence(expected_fence_token)
        _fence(new_fence_token)
        with self._transaction():
            self._require_identity(identity)
            if self._terminal(identity.idempotency_key) is not None:
                raise LedgerError("TERMINAL_OPERATION_IMMUTABLE")
            if self._start(identity.idempotency_key) is not None:
                raise LedgerError("OPERATION_STARTED_FENCE_IMMUTABLE")
            current = self._current_fence(identity.idempotency_key)
            if current != expected_fence_token:
                raise LedgerError("STALE_FENCE_TOKEN")
            if new_fence_token <= current:
                raise LedgerError("FENCE_TOKEN_NOT_MONOTONIC")
            self._insert(
                identity=identity,
                event_type="FENCE_REBOUND",
                terminal=0,
                fence_token=new_fence_token,
                reason_code="OPERATION_FENCE_REBOUND",
                touched_resource_refs=(),
                before_state_ref=None,
                after_state_ref=None,
                validation_status=None,
                external_dedupe_nonce=None,
                reservation_scope_hash=self._reservation_scope_hash(identity.idempotency_key),
            )
            return _event(self._latest(identity.idempotency_key))

    def mark_started(self, identity: OperationIdentity, *, fence_token: int, execution_grant_hash: str) -> ExecutionStartEvidence:
        _fence(fence_token)
        _scope_hash(execution_grant_hash)
        with self._transaction():
            self._require_identity(identity)
            if self._terminal(identity.idempotency_key) is not None:
                raise LedgerError("TERMINAL_OPERATION_IMMUTABLE")
            if self._current_fence(identity.idempotency_key) != fence_token:
                raise LedgerError("STALE_FENCE_TOKEN")
            scope_hash = self._reservation_scope_hash(identity.idempotency_key)
            existing = self._start(identity.idempotency_key)
            if existing is not None:
                evidence = _start_evidence(existing)
                if (
                    evidence.identity == identity
                    and evidence.fence_token == fence_token
                    and evidence.execution_grant_hash == execution_grant_hash
                    and evidence.reservation_scope_hash == scope_hash
                ):
                    return evidence
                raise LedgerError("EXECUTION_START_EVIDENCE_MISMATCH")
            self._db.execute(
                "INSERT INTO runner_vnext_operation_starts(idempotency_key,operation_id,target_state_ref,fence_token,execution_grant_hash,reservation_scope_hash) VALUES(?,?,?,?,?,?)",
                (identity.idempotency_key, identity.operation_id, identity.target_state_ref, fence_token, execution_grant_hash, scope_hash),
            )
            return _start_evidence(self._start(identity.idempotency_key))

    def started_event(self, idempotency_key: str) -> ExecutionStartEvidence:
        with self._lock:
            return _start_evidence(self._start(idempotency_key))

    def finish(self, identity: OperationIdentity, *, fence_token: int, success: bool, reason_code: str,
               touched_resource_refs: tuple[str, ...], before_state_ref: str, after_state_ref: str,
               validation_status: str) -> LedgerEvent:
        _fence(fence_token)
        _reason(reason_code)
        _public_refs(touched_resource_refs)
        _public_refs((before_state_ref, after_state_ref))
        if validation_status not in {"PASS", "FAIL"}:
            raise LedgerError("LEDGER_VALIDATION_STATUS_INVALID")
        with self._transaction():
            self._require_identity(identity)
            terminal = self._terminal(identity.idempotency_key)
            if terminal is not None:
                existing = _event(terminal)
                requested_type = "COMPLETED" if success else "FAILED"
                if (
                    existing.fence_token == fence_token
                    and existing.event_type == requested_type
                    and existing.reason_code == reason_code
                    and existing.touched_resource_refs == touched_resource_refs
                    and existing.before_state_ref == before_state_ref
                    and existing.after_state_ref == after_state_ref
                    and existing.validation_status == validation_status
                ):
                    return existing
                raise LedgerError("TERMINAL_RECEIPT_MISMATCH")
            if self._current_fence(identity.idempotency_key) != fence_token:
                raise LedgerError("STALE_FENCE_TOKEN")
            self._insert(
                identity=identity,
                event_type="COMPLETED" if success else "FAILED",
                terminal=1,
                fence_token=fence_token,
                reason_code=reason_code,
                touched_resource_refs=touched_resource_refs,
                before_state_ref=before_state_ref,
                after_state_ref=after_state_ref,
                validation_status=validation_status,
                external_dedupe_nonce=None,
                reservation_scope_hash=self._reservation_scope_hash(identity.idempotency_key),
            )
            return _event(self._terminal(identity.idempotency_key))

    def history(self, idempotency_key: str) -> tuple[LedgerEvent, ...]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM runner_vnext_operation_events WHERE idempotency_key=? ORDER BY sequence",
                (idempotency_key,),
            ).fetchall()
            return tuple(_event(row) for row in rows)

    def reservation_event(self, idempotency_key: str) -> LedgerEvent:
        with self._lock:
            return _event(self._reservation(idempotency_key))

    def current_fence_token(self, idempotency_key: str) -> int:
        with self._lock:
            return self._current_fence(idempotency_key)

    def status(self, idempotency_key: str) -> str | None:
        events = self.history(idempotency_key)
        if not events:
            return None
        terminal = [event for event in events if event.event_type in {"COMPLETED", "FAILED"}]
        if terminal:
            return terminal[-1].event_type
        with self._lock:
            return "STARTED" if self._start(idempotency_key) is not None else "RESERVED"

    def _reservation(self, key: str) -> sqlite3.Row | None:
        return self._db.execute(
            "SELECT * FROM runner_vnext_operation_events WHERE idempotency_key=? AND event_type='RESERVED'",
            (key,),
        ).fetchone()

    def _start(self, key: str) -> sqlite3.Row | None:
        return self._db.execute(
            "SELECT * FROM runner_vnext_operation_starts WHERE idempotency_key=?",
            (key,),
        ).fetchone()

    def _terminal(self, key: str) -> sqlite3.Row | None:
        return self._db.execute(
            "SELECT * FROM runner_vnext_operation_events WHERE idempotency_key=? AND terminal=1",
            (key,),
        ).fetchone()

    def _latest(self, key: str) -> sqlite3.Row:
        row = self._db.execute(
            "SELECT * FROM runner_vnext_operation_events WHERE idempotency_key=? ORDER BY sequence DESC LIMIT 1",
            (key,),
        ).fetchone()
        if row is None:
            raise LedgerError("OPERATION_NOT_RESERVED")
        return row

    def _current_fence(self, key: str) -> int:
        return int(self._latest(key)["fence_token"])

    def _reservation_scope_hash(self, key: str) -> str:
        row = self._reservation(key)
        if row is None or not row["reservation_scope_hash"]:
            raise LedgerError("RESERVATION_SCOPE_HASH_MISSING")
        return str(row["reservation_scope_hash"])

    def _require_identity(self, identity: OperationIdentity) -> None:
        row = self._reservation(identity.idempotency_key)
        if row is None:
            raise LedgerError("OPERATION_NOT_RESERVED")
        if _event(row).identity != identity:
            raise LedgerError("IDEMPOTENCY_TARGET_STATE_MISMATCH")

    def _insert(self, *, identity: OperationIdentity, event_type: str, terminal: int, fence_token: int,
                reason_code: str, touched_resource_refs: tuple[str, ...], before_state_ref: str | None,
                after_state_ref: str | None, validation_status: str | None,
                external_dedupe_nonce: str | None, reservation_scope_hash: str | None) -> None:
        import json
        self._db.execute(
            "INSERT INTO runner_vnext_operation_events(operation_id,idempotency_key,target_state_ref,event_type,terminal,fence_token,reason_code,touched_resource_refs,before_state_ref,after_state_ref,validation_status,external_dedupe_nonce,reservation_scope_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (identity.operation_id, identity.idempotency_key, identity.target_state_ref, event_type, terminal,
             fence_token, reason_code, json.dumps(touched_resource_refs), before_state_ref, after_state_ref,
             validation_status, external_dedupe_nonce, reservation_scope_hash),
        )


def _start_evidence(row: sqlite3.Row | None) -> ExecutionStartEvidence:
    if row is None:
        raise LedgerError("EXECUTION_START_EVIDENCE_MISSING")
    return ExecutionStartEvidence(
        identity=OperationIdentity(row["operation_id"], row["idempotency_key"], row["target_state_ref"]),
        fence_token=int(row["fence_token"]),
        execution_grant_hash=row["execution_grant_hash"],
        reservation_scope_hash=row["reservation_scope_hash"],
    )


def _event(row: sqlite3.Row | None) -> LedgerEvent:
    if row is None:
        raise LedgerError("LEDGER_EVENT_MISSING")
    import json
    return LedgerEvent(
        sequence=int(row["sequence"]),
        identity=OperationIdentity(row["operation_id"], row["idempotency_key"], row["target_state_ref"]),
        event_type=row["event_type"],
        fence_token=int(row["fence_token"]),
        reason_code=row["reason_code"],
        touched_resource_refs=tuple(json.loads(row["touched_resource_refs"])),
        before_state_ref=row["before_state_ref"],
        after_state_ref=row["after_state_ref"],
        validation_status=row["validation_status"],
        external_dedupe_nonce=row["external_dedupe_nonce"],
        reservation_scope_hash=row["reservation_scope_hash"],
    )


def _identity(identity: OperationIdentity) -> None:
    if not identity.operation_id or not identity.idempotency_key or not identity.target_state_ref:
        raise LedgerError("OPERATION_IDENTITY_REQUIRED")
    _public_refs((identity.target_state_ref,))


def _fence(value: int) -> None:
    if not isinstance(value, int) or value < 1:
        raise LedgerError("FENCE_TOKEN_INVALID")


def _reason(value: str) -> None:
    if not value or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for ch in value):
        raise LedgerError("LEDGER_REASON_CODE_INVALID")


def _nonce(value: str | None) -> None:
    if value is not None and (not value or len(value) > 128 or any(ch.isspace() for ch in value)):
        raise LedgerError("EXTERNAL_DEDUPE_NONCE_INVALID")


def _public_refs(refs: tuple[str, ...]) -> None:
    for ref in refs:
        if not ref or ref.startswith(("/", "~")) or "\\" in ref or ".." in ref or ":" not in ref:
            raise LedgerError("PUBLIC_RESOURCE_REF_REQUIRED")


def _scope_hash(value: str) -> None:
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise LedgerError("RESERVATION_SCOPE_HASH_INVALID")
