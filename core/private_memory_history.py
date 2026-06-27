from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


CANONICAL_REVISION = "skeleton.private_memory.canonical_revision.v1"
MEMORY_EVENT = "skeleton.private_memory.memory_event.v1"
FACT_HISTORY_ENTRY = "skeleton.private_memory.fact_history_entry.v1"
TOMBSTONE_EVENT = "skeleton.private_memory.tombstone_event.v1"
INTEGRITY_REPORT = "skeleton.private_memory.integrity_report.v1"

ZERO_HASH = "0" * 64
SCHEMA_VERSION = "skeleton.private_memory.sqlite.v1"

_SAFE_EVENT_TYPES = frozenset({"create", "update", "supersede", "revoke", "delete"})
_SAFE_TOKEN_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")
_REQUIRED_TABLE_SQL = {
    "private_memory_meta": """
        CREATE TABLE private_memory_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """,
    "private_memory_canonical_revision": """
        CREATE TABLE private_memory_canonical_revision (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            schema TEXT NOT NULL,
            current_revision INTEGER NOT NULL CHECK (current_revision >= 0),
            updated_at TEXT NOT NULL
        )
    """,
    "private_memory_facts": """
        CREATE TABLE private_memory_facts (
            namespace TEXT NOT NULL,
            fact_id TEXT NOT NULL,
            value_json TEXT NOT NULL,
            value_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            canonical_revision INTEGER NOT NULL,
            tombstoned_at TEXT,
            tombstone_reason TEXT,
            PRIMARY KEY (namespace, fact_id)
        )
    """,
    "private_memory_events": """
        CREATE TABLE private_memory_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            schema TEXT NOT NULL,
            event_type TEXT NOT NULL,
            namespace TEXT NOT NULL,
            fact_id TEXT NOT NULL,
            actor_ref TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            approval_ref TEXT NOT NULL,
            transaction_ref TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            new_hash TEXT NOT NULL,
            canonical_revision INTEGER NOT NULL UNIQUE
        )
    """,
    "private_memory_fact_history": """
        CREATE TABLE private_memory_fact_history (
            history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            schema TEXT NOT NULL,
            event_type TEXT NOT NULL,
            namespace TEXT NOT NULL,
            fact_id TEXT NOT NULL,
            previous_value_json TEXT,
            new_value_json TEXT,
            previous_hash TEXT NOT NULL,
            new_hash TEXT NOT NULL,
            canonical_revision INTEGER NOT NULL UNIQUE,
            timestamp TEXT NOT NULL
        )
    """,
    "private_memory_tombstones": """
        CREATE TABLE private_memory_tombstones (
            tombstone_id INTEGER PRIMARY KEY AUTOINCREMENT,
            schema TEXT NOT NULL,
            namespace TEXT NOT NULL,
            fact_id TEXT NOT NULL,
            actor_ref TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            approval_ref TEXT NOT NULL,
            transaction_ref TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            new_hash TEXT NOT NULL,
            canonical_revision INTEGER NOT NULL UNIQUE
        )
    """,
}
_REQUIRED_INDEXES = frozenset(
    {
        "private_memory_events_fact_revision_idx",
        "private_memory_history_fact_revision_idx",
        "private_memory_tombstones_fact_revision_idx",
    }
)
_REQUIRED_INDEX_SQL = {
    "private_memory_events_fact_revision_idx": """
        CREATE INDEX private_memory_events_fact_revision_idx
        ON private_memory_events (namespace, fact_id, canonical_revision)
    """,
    "private_memory_history_fact_revision_idx": """
        CREATE INDEX private_memory_history_fact_revision_idx
        ON private_memory_fact_history (namespace, fact_id, canonical_revision)
    """,
    "private_memory_tombstones_fact_revision_idx": """
        CREATE INDEX private_memory_tombstones_fact_revision_idx
        ON private_memory_tombstones (namespace, fact_id, canonical_revision)
    """,
}
_REQUIRED_TRIGGERS = frozenset(
    {
        "private_memory_no_fact_delete",
        "private_memory_no_event_update",
        "private_memory_no_event_delete",
        "private_memory_no_history_update",
        "private_memory_no_history_delete",
        "private_memory_no_tombstone_update",
        "private_memory_no_tombstone_delete",
    }
)
_REQUIRED_TRIGGER_SQL = {
    "private_memory_no_fact_delete": """
        CREATE TRIGGER private_memory_no_fact_delete
        BEFORE DELETE ON private_memory_facts
        BEGIN
            SELECT RAISE(ABORT, 'canonical fact delete forbidden');
        END
    """,
    "private_memory_no_event_update": """
        CREATE TRIGGER private_memory_no_event_update
        BEFORE UPDATE ON private_memory_events
        BEGIN
            SELECT RAISE(ABORT, 'canonical event update forbidden');
        END
    """,
    "private_memory_no_event_delete": """
        CREATE TRIGGER private_memory_no_event_delete
        BEFORE DELETE ON private_memory_events
        BEGIN
            SELECT RAISE(ABORT, 'canonical event delete forbidden');
        END
    """,
    "private_memory_no_history_update": """
        CREATE TRIGGER private_memory_no_history_update
        BEFORE UPDATE ON private_memory_fact_history
        BEGIN
            SELECT RAISE(ABORT, 'canonical history update forbidden');
        END
    """,
    "private_memory_no_history_delete": """
        CREATE TRIGGER private_memory_no_history_delete
        BEFORE DELETE ON private_memory_fact_history
        BEGIN
            SELECT RAISE(ABORT, 'canonical history delete forbidden');
        END
    """,
    "private_memory_no_tombstone_update": """
        CREATE TRIGGER private_memory_no_tombstone_update
        BEFORE UPDATE ON private_memory_tombstones
        BEGIN
            SELECT RAISE(ABORT, 'canonical tombstone update forbidden');
        END
    """,
    "private_memory_no_tombstone_delete": """
        CREATE TRIGGER private_memory_no_tombstone_delete
        BEFORE DELETE ON private_memory_tombstones
        BEGIN
            SELECT RAISE(ABORT, 'canonical tombstone delete forbidden');
        END
    """,
}


@dataclass(frozen=True)
class MemoryEvent:
    schema: str
    event_type: str
    namespace: str
    fact_id: str
    actor_ref: str
    reason_code: str
    approval_ref: str
    transaction_ref: str
    timestamp: str
    previous_hash: str
    new_hash: str
    canonical_revision: int


@dataclass(frozen=True)
class IntegrityReport:
    schema: str
    status: str
    integrity_ok: bool
    revision_ok: bool
    history_ok: bool
    destructive_mutation_ok: bool
    canonical_revision: int
    event_count: int
    fact_count: int
    tombstone_count: int
    error_class: str | None
    next_operator_action: str


class PrivateMemoryHistoryError(Exception):
    """Base exception for canonical private memory history failures."""


class PrivateMemoryHistoryGapError(PrivateMemoryHistoryError):
    """Raised when canonical event revisions are missing or duplicated."""


class PrivateMemoryRevisionRegressionError(PrivateMemoryHistoryError):
    """Raised when the canonical revision counter regresses."""


class PrivateMemoryDestructiveMutationError(PrivateMemoryHistoryError):
    """Raised when a destructive canonical mutation is attempted or detected."""


class PrivateMemoryIntegrityFailure(PrivateMemoryHistoryError):
    """Raised when sanitized integrity checks fail closed."""


def ensure_history_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS private_memory_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        INSERT OR IGNORE INTO private_memory_meta (key, value)
        VALUES ('schema_version', 'skeleton.private_memory.sqlite.v1');

        CREATE TABLE IF NOT EXISTS private_memory_canonical_revision (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            schema TEXT NOT NULL,
            current_revision INTEGER NOT NULL CHECK (current_revision >= 0),
            updated_at TEXT NOT NULL
        );

        INSERT OR IGNORE INTO private_memory_canonical_revision
            (id, schema, current_revision, updated_at)
        VALUES (1, 'skeleton.private_memory.canonical_revision.v1', 0, '1970-01-01T00:00:00Z');

        CREATE TABLE IF NOT EXISTS private_memory_facts (
            namespace TEXT NOT NULL,
            fact_id TEXT NOT NULL,
            value_json TEXT NOT NULL,
            value_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            canonical_revision INTEGER NOT NULL,
            tombstoned_at TEXT,
            tombstone_reason TEXT,
            PRIMARY KEY (namespace, fact_id)
        );

        CREATE TABLE IF NOT EXISTS private_memory_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            schema TEXT NOT NULL,
            event_type TEXT NOT NULL,
            namespace TEXT NOT NULL,
            fact_id TEXT NOT NULL,
            actor_ref TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            approval_ref TEXT NOT NULL,
            transaction_ref TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            new_hash TEXT NOT NULL,
            canonical_revision INTEGER NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS private_memory_fact_history (
            history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            schema TEXT NOT NULL,
            event_type TEXT NOT NULL,
            namespace TEXT NOT NULL,
            fact_id TEXT NOT NULL,
            previous_value_json TEXT,
            new_value_json TEXT,
            previous_hash TEXT NOT NULL,
            new_hash TEXT NOT NULL,
            canonical_revision INTEGER NOT NULL UNIQUE,
            timestamp TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS private_memory_tombstones (
            tombstone_id INTEGER PRIMARY KEY AUTOINCREMENT,
            schema TEXT NOT NULL,
            namespace TEXT NOT NULL,
            fact_id TEXT NOT NULL,
            actor_ref TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            approval_ref TEXT NOT NULL,
            transaction_ref TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            new_hash TEXT NOT NULL,
            canonical_revision INTEGER NOT NULL UNIQUE
        );

        CREATE INDEX IF NOT EXISTS private_memory_events_fact_revision_idx
        ON private_memory_events (namespace, fact_id, canonical_revision);

        CREATE INDEX IF NOT EXISTS private_memory_history_fact_revision_idx
        ON private_memory_fact_history (namespace, fact_id, canonical_revision);

        CREATE INDEX IF NOT EXISTS private_memory_tombstones_fact_revision_idx
        ON private_memory_tombstones (namespace, fact_id, canonical_revision);

        CREATE TRIGGER IF NOT EXISTS private_memory_no_fact_delete
        BEFORE DELETE ON private_memory_facts
        BEGIN
            SELECT RAISE(ABORT, 'canonical fact delete forbidden');
        END;

        CREATE TRIGGER IF NOT EXISTS private_memory_no_event_update
        BEFORE UPDATE ON private_memory_events
        BEGIN
            SELECT RAISE(ABORT, 'canonical event update forbidden');
        END;

        CREATE TRIGGER IF NOT EXISTS private_memory_no_event_delete
        BEFORE DELETE ON private_memory_events
        BEGIN
            SELECT RAISE(ABORT, 'canonical event delete forbidden');
        END;

        CREATE TRIGGER IF NOT EXISTS private_memory_no_history_update
        BEFORE UPDATE ON private_memory_fact_history
        BEGIN
            SELECT RAISE(ABORT, 'canonical history update forbidden');
        END;

        CREATE TRIGGER IF NOT EXISTS private_memory_no_history_delete
        BEFORE DELETE ON private_memory_fact_history
        BEGIN
            SELECT RAISE(ABORT, 'canonical history delete forbidden');
        END;

        CREATE TRIGGER IF NOT EXISTS private_memory_no_tombstone_update
        BEFORE UPDATE ON private_memory_tombstones
        BEGIN
            SELECT RAISE(ABORT, 'canonical tombstone update forbidden');
        END;

        CREATE TRIGGER IF NOT EXISTS private_memory_no_tombstone_delete
        BEFORE DELETE ON private_memory_tombstones
        BEGIN
            SELECT RAISE(ABORT, 'canonical tombstone delete forbidden');
        END;
        """
    )
    connection.commit()


def enable_wal_if_supported(connection: sqlite3.Connection) -> bool:
    try:
        row = connection.execute("PRAGMA journal_mode=WAL").fetchone()
    except sqlite3.DatabaseError:
        return False
    return row is not None and str(row[0]).lower() == "wal"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def bytes_hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_token(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError(f"{name} must be a non-empty synthetic token")
    if any(char not in _SAFE_TOKEN_CHARS for char in value):
        raise ValueError(f"{name} must be public-safe")
    lowered = value.lower()
    if any(marker in lowered for marker in ("secret", "token", "password", "credential", "/", "\\")):
        raise ValueError(f"{name} must not contain private markers")
    return value


def safe_event_type(event_type: str) -> str:
    if event_type not in _SAFE_EVENT_TYPES:
        raise ValueError("unsupported canonical event type")
    return event_type


def append_history_event(
    connection: sqlite3.Connection,
    *,
    event_type: str,
    namespace: str,
    fact_id: str,
    actor_ref: str,
    reason_code: str,
    approval_ref: str,
    transaction_ref: str,
    timestamp: str,
    previous_hash: str,
    new_hash: str,
    canonical_revision: int,
    previous_value_json: str | None,
    new_value_json: str | None,
) -> MemoryEvent:
    event = MemoryEvent(
        schema=MEMORY_EVENT,
        event_type=safe_event_type(event_type),
        namespace=safe_token(namespace, "namespace"),
        fact_id=safe_token(fact_id, "fact_id"),
        actor_ref=safe_token(actor_ref, "actor_ref"),
        reason_code=safe_token(reason_code, "reason_code"),
        approval_ref=safe_token(approval_ref, "approval_ref"),
        transaction_ref=safe_token(transaction_ref, "transaction_ref"),
        timestamp=timestamp,
        previous_hash=previous_hash,
        new_hash=new_hash,
        canonical_revision=canonical_revision,
    )
    connection.execute(
        """
        INSERT INTO private_memory_events (
            schema, event_type, namespace, fact_id, actor_ref, reason_code,
            approval_ref, transaction_ref, timestamp, previous_hash, new_hash,
            canonical_revision
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.schema,
            event.event_type,
            event.namespace,
            event.fact_id,
            event.actor_ref,
            event.reason_code,
            event.approval_ref,
            event.transaction_ref,
            event.timestamp,
            event.previous_hash,
            event.new_hash,
            event.canonical_revision,
        ),
    )
    connection.execute(
        """
        INSERT INTO private_memory_fact_history (
            schema, event_type, namespace, fact_id, previous_value_json,
            new_value_json, previous_hash, new_hash, canonical_revision, timestamp
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            FACT_HISTORY_ENTRY,
            event.event_type,
            event.namespace,
            event.fact_id,
            previous_value_json,
            new_value_json,
            event.previous_hash,
            event.new_hash,
            event.canonical_revision,
            event.timestamp,
        ),
    )
    if event.event_type in {"revoke", "delete"}:
        connection.execute(
            """
            INSERT INTO private_memory_tombstones (
                schema, namespace, fact_id, actor_ref, reason_code, approval_ref,
                transaction_ref, timestamp, previous_hash, new_hash, canonical_revision
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                TOMBSTONE_EVENT,
                event.namespace,
                event.fact_id,
                event.actor_ref,
                event.reason_code,
                event.approval_ref,
                event.transaction_ref,
                event.timestamp,
                event.previous_hash,
                event.new_hash,
                event.canonical_revision,
            ),
        )
    return event


def sanitized_integrity_report(connection: sqlite3.Connection) -> dict[str, object]:
    try:
        validate_history_schema(connection)
        sqlite_ok = _sqlite_integrity_ok(connection)
        revision = current_revision(connection)
        event_count = _count(connection, "private_memory_events")
        fact_count = _count(connection, "private_memory_facts")
        tombstone_count = _count(connection, "private_memory_tombstones")
        revision_ok = _revision_sequence_ok(connection, revision, event_count)
        history_ok = _history_chain_ok(connection)
        destructive_ok = history_ok and _destructive_mutation_ok(connection)
        status = "DONE" if sqlite_ok and revision_ok and destructive_ok and history_ok else "BLOCKED"
        error_class = None if status == "DONE" else "PrivateMemoryIntegrityFailure"
        report = IntegrityReport(
            schema=INTEGRITY_REPORT,
            status=status,
            integrity_ok=sqlite_ok,
            revision_ok=revision_ok,
            history_ok=history_ok,
            destructive_mutation_ok=destructive_ok,
            canonical_revision=revision,
            event_count=event_count,
            fact_count=fact_count,
            tombstone_count=tombstone_count,
            error_class=error_class,
            next_operator_action="none" if status == "DONE" else "inspect_private_memory_recovery",
        )
        return asdict(report)
    except Exception as exc:  # noqa: BLE001 - reports must fail closed and sanitized.
        return asdict(
            IntegrityReport(
                schema=INTEGRITY_REPORT,
                status="BLOCKED",
                integrity_ok=False,
                revision_ok=False,
                history_ok=False,
                destructive_mutation_ok=False,
                canonical_revision=0,
                event_count=0,
                fact_count=0,
                tombstone_count=0,
                error_class=type(exc).__name__,
                next_operator_action="inspect_private_memory_recovery",
            )
        )


def verify_integrity_or_raise(connection: sqlite3.Connection) -> None:
    report = sanitized_integrity_report(connection)
    if report["status"] != "DONE":
        raise PrivateMemoryIntegrityFailure(str(report["error_class"]))


def verify_existing_integrity_or_raise(connection: sqlite3.Connection) -> None:
    try:
        validate_history_schema(connection)
        sqlite_ok = _sqlite_integrity_ok(connection)
        revision = current_revision(connection)
        event_count = _count(connection, "private_memory_events")
        revision_ok = _revision_sequence_ok(connection, revision, event_count)
        history_ok = _history_chain_ok(connection)
        destructive_ok = history_ok and _destructive_mutation_ok(connection)
    except Exception as exc:  # noqa: BLE001 - fail closed for public callers.
        raise PrivateMemoryIntegrityFailure(type(exc).__name__) from exc
    if not (sqlite_ok and revision_ok and history_ok and destructive_ok):
        raise PrivateMemoryIntegrityFailure("PrivateMemoryIntegrityFailure")


def current_revision(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT current_revision FROM private_memory_canonical_revision WHERE id = 1"
    ).fetchone()
    if row is None:
        raise PrivateMemoryRevisionRegressionError("missing revision row")
    return int(row[0])


def next_revision(connection: sqlite3.Connection, *, timestamp: str) -> int:
    revision = current_revision(connection)
    next_value = revision + 1
    cursor = connection.execute(
        """
        UPDATE private_memory_canonical_revision
        SET current_revision = ?, updated_at = ?
        WHERE id = 1 AND current_revision = ?
        """,
        (next_value, timestamp, revision),
    )
    if cursor.rowcount != 1:
        raise PrivateMemoryRevisionRegressionError("canonical revision update failed")
    return next_value


def _sqlite_integrity_ok(connection: sqlite3.Connection) -> bool:
    row = connection.execute("PRAGMA integrity_check").fetchone()
    return row is not None and row[0] == "ok"


def _revision_sequence_ok(connection: sqlite3.Connection, revision: int, event_count: int) -> bool:
    if revision != event_count:
        return False
    row = connection.execute(
        """
        SELECT schema, current_revision, updated_at
        FROM private_memory_canonical_revision
        WHERE id = 1
        """
    ).fetchone()
    if row is None or str(row["schema"]) != CANONICAL_REVISION:
        return False
    if int(row["current_revision"]) != revision:
        return False
    rows = connection.execute(
        "SELECT canonical_revision FROM private_memory_events ORDER BY canonical_revision"
    ).fetchall()
    event_revisions = [int(row[0]) for row in rows]
    if event_revisions != list(range(1, revision + 1)):
        return False
    if revision == 0:
        if str(row["updated_at"]) != "1970-01-01T00:00:00Z":
            return False
    else:
        latest_event = connection.execute(
            "SELECT timestamp FROM private_memory_events WHERE canonical_revision = ?",
            (revision,),
        ).fetchone()
        if latest_event is None or str(row["updated_at"]) != str(latest_event["timestamp"]):
            return False
    history_rows = connection.execute(
        """
        SELECT canonical_revision
        FROM private_memory_fact_history
        ORDER BY canonical_revision
        """
    ).fetchall()
    return [int(row[0]) for row in history_rows] == event_revisions


def validate_history_schema(connection: sqlite3.Connection) -> None:
    table_rows = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    tables = {str(row[0]): row[1] for row in table_rows}
    if not set(_REQUIRED_TABLE_SQL).issubset(tables):
        raise PrivateMemoryIntegrityFailure("missing canonical schema table")
    for table_name, expected_sql in _REQUIRED_TABLE_SQL.items():
        if _normalized_sql(tables[table_name]) != _normalized_sql(expected_sql):
            raise PrivateMemoryIntegrityFailure("modified canonical schema table")

    meta = connection.execute(
        "SELECT value FROM private_memory_meta WHERE key = 'schema_version'"
    ).fetchone()
    if meta is None or str(meta[0]) != SCHEMA_VERSION:
        raise PrivateMemoryIntegrityFailure("missing canonical schema metadata")

    revision = connection.execute(
        """
        SELECT schema, current_revision, updated_at
        FROM private_memory_canonical_revision
        WHERE id = 1
        """
    ).fetchone()
    if revision is None or str(revision[0]) != CANONICAL_REVISION:
        raise PrivateMemoryIntegrityFailure("missing canonical revision metadata")

    index_rows = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'index'"
    ).fetchall()
    indexes = {str(row[0]): row[1] for row in index_rows}
    if not _REQUIRED_INDEXES.issubset(indexes):
        raise PrivateMemoryIntegrityFailure("missing canonical schema index")
    for index_name, expected_sql in _REQUIRED_INDEX_SQL.items():
        if _normalized_sql(indexes[index_name]) != _normalized_sql(expected_sql):
            raise PrivateMemoryIntegrityFailure("modified canonical schema index")

    trigger_rows = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger'"
    ).fetchall()
    triggers = {str(row[0]): row[1] for row in trigger_rows}
    if not _REQUIRED_TRIGGERS.issubset(triggers):
        raise PrivateMemoryIntegrityFailure("missing canonical schema trigger")
    for trigger_name, expected_sql in _REQUIRED_TRIGGER_SQL.items():
        if _normalized_sql(triggers[trigger_name]) != _normalized_sql(expected_sql):
            raise PrivateMemoryIntegrityFailure("modified canonical schema trigger")


def _destructive_mutation_ok(connection: sqlite3.Connection) -> bool:
    rows = connection.execute(
        """
        SELECT canonical_revision, event_type
        FROM private_memory_events
        WHERE event_type IN ('revoke', 'delete')
        ORDER BY canonical_revision
        """
    ).fetchall()
    for row in rows:
        tombstone = connection.execute(
            """
            SELECT schema, namespace, fact_id, actor_ref, reason_code, approval_ref,
                transaction_ref, timestamp, previous_hash, new_hash
            FROM private_memory_tombstones
            WHERE canonical_revision = ?
            """,
            (int(row[0]),),
        ).fetchone()
        if tombstone is None:
            return False
        event = connection.execute(
            """
            SELECT schema, namespace, fact_id, actor_ref, reason_code, approval_ref,
                transaction_ref, timestamp, previous_hash, new_hash
            FROM private_memory_events
            WHERE canonical_revision = ?
            """,
            (int(row[0]),),
        ).fetchone()
        if event is None or str(tombstone["schema"]) != TOMBSTONE_EVENT:
            return False
        for key in (
            "namespace",
            "fact_id",
            "actor_ref",
            "reason_code",
            "approval_ref",
            "transaction_ref",
            "timestamp",
            "previous_hash",
            "new_hash",
        ):
            if str(tombstone[key]) != str(event[key]):
                return False
        if str(tombstone["new_hash"]) != ZERO_HASH:
            return False
    extra = connection.execute(
        """
        SELECT t.canonical_revision
        FROM private_memory_tombstones AS t
        LEFT JOIN private_memory_events AS e
            ON e.canonical_revision = t.canonical_revision
            AND e.event_type IN ('revoke', 'delete')
        WHERE e.canonical_revision IS NULL
        LIMIT 1
        """
    ).fetchone()
    if extra is not None:
        return False
    return True


def canonical_logical_state(connection: sqlite3.Connection) -> dict[str, object]:
    """Return the complete canonical state used for private integrity proofs."""
    return {
        "schema_version": _database_schema_version(connection),
        "revision": _rows(
            connection,
            """
            SELECT schema, current_revision, updated_at
            FROM private_memory_canonical_revision
            WHERE id = 1
            """,
        ),
        "facts": _rows(
            connection,
            """
            SELECT namespace, fact_id, value_json, value_hash, created_at, updated_at,
                canonical_revision, tombstoned_at, tombstone_reason
            FROM private_memory_facts
            ORDER BY namespace, fact_id
            """,
        ),
        "events": _rows(
            connection,
            """
            SELECT schema, event_type, namespace, fact_id, actor_ref, reason_code,
                approval_ref, transaction_ref, timestamp, previous_hash, new_hash,
                canonical_revision
            FROM private_memory_events
            ORDER BY canonical_revision
            """,
        ),
        "history": _rows(
            connection,
            """
            SELECT schema, event_type, namespace, fact_id, previous_value_json,
                new_value_json, previous_hash, new_hash, canonical_revision, timestamp
            FROM private_memory_fact_history
            ORDER BY canonical_revision
            """,
        ),
        "tombstones": _rows(
            connection,
            """
            SELECT schema, namespace, fact_id, actor_ref, reason_code, approval_ref,
                transaction_ref, timestamp, previous_hash, new_hash, canonical_revision
            FROM private_memory_tombstones
            ORDER BY canonical_revision
            """,
        ),
    }


def canonical_logical_state_digest(connection: sqlite3.Connection) -> str:
    return content_hash(canonical_logical_state(connection))


def _history_chain_ok(connection: sqlite3.Connection) -> bool:
    rows = connection.execute(
        """
        SELECT e.schema AS event_schema, e.event_type, e.namespace, e.fact_id,
            e.actor_ref, e.reason_code, e.approval_ref, e.transaction_ref,
            e.timestamp, e.previous_hash, e.new_hash, e.canonical_revision,
            h.schema AS history_schema, h.previous_value_json, h.new_value_json,
            h.previous_hash AS history_previous_hash, h.new_hash AS history_new_hash,
            h.timestamp AS history_timestamp
        FROM private_memory_events AS e
        JOIN private_memory_fact_history AS h
            ON h.canonical_revision = e.canonical_revision
        ORDER BY e.canonical_revision
        """
    ).fetchall()
    if len(rows) != _count(connection, "private_memory_events"):
        return False

    last_hash_by_fact: dict[tuple[str, str], str] = {}
    first_history_by_fact: dict[tuple[str, str], sqlite3.Row] = {}
    latest_history_by_fact: dict[tuple[str, str], sqlite3.Row] = {}
    for row in rows:
        if str(row["event_schema"]) != MEMORY_EVENT:
            return False
        if str(row["history_schema"]) != FACT_HISTORY_ENTRY:
            return False
        if str(row["event_type"]) not in _SAFE_EVENT_TYPES:
            return False
        for key in ("namespace", "fact_id", "actor_ref", "reason_code", "approval_ref", "transaction_ref"):
            safe_token(str(row[key]), key)
        for key in ("event_type", "namespace", "fact_id"):
            if str(row[key]) != str(row[key]):
                return False
        for key in ("event_type", "namespace", "fact_id"):
            history_value = connection.execute(
                f"SELECT {key} FROM private_memory_fact_history WHERE canonical_revision = ?",
                (int(row["canonical_revision"]),),
            ).fetchone()
            if history_value is None or str(history_value[0]) != str(row[key]):
                return False
        if str(row["timestamp"]) != str(row["history_timestamp"]):
            return False
        if str(row["previous_hash"]) != str(row["history_previous_hash"]):
            return False
        if str(row["new_hash"]) != str(row["history_new_hash"]):
            return False

        key = (str(row["namespace"]), str(row["fact_id"]))
        first_history_by_fact.setdefault(key, row)
        expected_previous = last_hash_by_fact.get(key, ZERO_HASH)
        if str(row["previous_hash"]) != expected_previous:
            return False
        new_value_json = row["new_value_json"]
        if str(row["event_type"]) in {"revoke", "delete"}:
            if new_value_json is not None or str(row["new_hash"]) != ZERO_HASH:
                return False
        else:
            if new_value_json is None:
                return False
            try:
                if content_hash(json.loads(str(new_value_json))) != str(row["new_hash"]):
                    return False
            except (TypeError, json.JSONDecodeError):
                return False
        previous_value_json = row["previous_value_json"]
        if previous_value_json is None:
            if str(row["previous_hash"]) != ZERO_HASH:
                return False
        else:
            try:
                if content_hash(json.loads(str(previous_value_json))) != str(row["previous_hash"]):
                    return False
            except (TypeError, json.JSONDecodeError):
                return False
        if str(row["event_type"]) in {"revoke", "delete"}:
            last_hash_by_fact[key] = str(row["previous_hash"])
        else:
            last_hash_by_fact[key] = str(row["new_hash"])
        latest_history_by_fact[key] = row

    fact_rows = connection.execute(
        """
        SELECT namespace, fact_id, value_json, value_hash, created_at, updated_at,
            canonical_revision, tombstoned_at, tombstone_reason
        FROM private_memory_facts
        ORDER BY namespace, fact_id
        """
    ).fetchall()
    fact_keys = {(str(row["namespace"]), str(row["fact_id"])) for row in fact_rows}
    if len(fact_rows) != len(latest_history_by_fact) or fact_keys != set(latest_history_by_fact):
        return False
    for fact in fact_rows:
        key = (str(fact["namespace"]), str(fact["fact_id"]))
        latest = latest_history_by_fact.get(key)
        first = first_history_by_fact.get(key)
        if latest is None or first is None:
            return False
        if str(fact["created_at"]) != str(first["timestamp"]):
            return False
        if int(fact["canonical_revision"]) != int(latest["canonical_revision"]):
            return False
        if str(fact["updated_at"]) != str(latest["timestamp"]):
            return False
        try:
            if content_hash(json.loads(str(fact["value_json"]))) != str(fact["value_hash"]):
                return False
        except (TypeError, json.JSONDecodeError):
            return False
        tombstoned = fact["tombstoned_at"] is not None
        destructive_latest = str(latest["event_type"]) in {"revoke", "delete"}
        if tombstoned != destructive_latest:
            return False
        expected_fact_hash = str(latest["previous_hash"] if tombstoned else latest["new_hash"])
        if str(fact["value_hash"]) != expected_fact_hash:
            return False
        if tombstoned and str(fact["tombstoned_at"]) != str(latest["timestamp"]):
            return False
        if tombstoned and str(fact["tombstone_reason"]) != str(latest["reason_code"]):
            return False
        if not tombstoned and fact["tombstone_reason"] is not None:
            return False
    return True


def _database_schema_version(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT value FROM private_memory_meta WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        raise PrivateMemoryIntegrityFailure("missing schema version")
    return str(row[0])


def _rows(connection: sqlite3.Connection, query: str) -> list[dict[str, object]]:
    return [dict(row) for row in connection.execute(query).fetchall()]


def _count(connection: sqlite3.Connection, table_name: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def _normalized_sql(sql: object) -> str:
    if not isinstance(sql, str):
        return ""
    return " ".join(sql.split()).lower()
