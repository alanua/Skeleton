from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from core.private_memory_history import (
    ZERO_HASH,
    SCHEMA_VERSION,
    append_history_event,
    bytes_hash,
    canonical_logical_state_digest,
    canonical_json,
    content_hash,
    current_revision,
    enable_wal_if_supported,
    ensure_history_schema,
    next_revision,
    safe_event_type,
    safe_token,
    sanitized_integrity_report,
    utc_now,
    verify_existing_integrity_or_raise,
    verify_integrity_or_raise,
)


PRIVATE_MEMORY_CONFIG_ENV = "SKELETON_PRIVATE_MEMORY_CONFIG"
PRIVATE_MEMORY_CONFIG_SCHEMA = "skeleton.private_memory.config.v0"
PRIVATE_MEMORY_BOOTSTRAP_REGISTRY_SCHEMAS = frozenset(
    {
        "skeleton.bootstrap.local_registry.v0",
        "skeleton.local_registry.v0",
        "skeleton.private_memory.local_registry.v0",
    }
)
PRIVATE_MEMORY_HEALTHCHECK_SCHEMA = "skeleton.private_memory.healthcheck.v0"
PRIVATE_MEMORY_SNAPSHOT_MANIFEST_SCHEMA = "skeleton.private_memory.snapshot_manifest.v1"

_HEARTBEAT_TABLE = "private_memory_heartbeat"
_TASK_STATE_TABLE = "private_memory_task_state_heartbeat"
_CONNECTOR_TABLES = frozenset({_HEARTBEAT_TABLE, _TASK_STATE_TABLE})
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.: -]{0,127}$")
_UNSAFE_REPORT_KEYS = frozenset(
    {
        "content",
        "credential",
        "credentials",
        "db_path",
        "env",
        "path",
        "payload",
        "registry",
        "root",
        "secret",
        "secrets",
        "sql",
        "table",
        "table_name",
        "tables",
        "token",
        "tokens",
    }
)


@dataclass(frozen=True)
class PrivateMemoryHealthcheck:
    schema: str
    status: str
    db_configured: bool
    db_openable: bool
    integrity_ok: bool
    schema_present: bool
    table_count: int
    writable_when_requested: bool
    heartbeat_ok: bool
    error_class: str | None
    next_operator_action: str


@dataclass(frozen=True)
class _ValidatedBulkFact:
    namespace: str
    fact_id: str
    value_json: str
    value_hash: str


def healthcheck_private_memory(
    config_path: str | Path | None = None,
    *,
    request_write: bool = False,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Return a public-safe private memory status report."""
    connector = PrivateMemoryConnector(config_path=config_path, env=env)
    return connector.healthcheck(request_write=request_write)


def write_public_heartbeat(
    heartbeat_id: str,
    *,
    source: str = "synthetic",
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Write and verify one synthetic public-safe heartbeat."""
    connector = PrivateMemoryConnector(config_path=config_path, env=env)
    return connector.write_heartbeat(heartbeat_id=heartbeat_id, source=source)


def read_public_heartbeat(
    heartbeat_id: str,
    *,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Read one synthetic public-safe heartbeat status without exposing rows."""
    connector = PrivateMemoryConnector(config_path=config_path, env=env)
    return connector.read_heartbeat(heartbeat_id=heartbeat_id)


def record_task_state_heartbeat(
    task_id: str,
    state: str,
    *,
    source: str = "synthetic",
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Write and verify one synthetic public-safe task-state heartbeat."""
    connector = PrivateMemoryConnector(config_path=config_path, env=env)
    return connector.record_task_state_heartbeat(task_id=task_id, state=state, source=source)


class CanonicalPrivateMemoryStore:
    """Revisioned append-only canonical fact store for local private SQLite."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> dict[str, object]:
        with closing(self._connect(write=True)) as connection:
            ensure_history_schema(connection)
            wal_enabled = enable_wal_if_supported(connection)
            report = sanitized_integrity_report(connection)
            report["wal_enabled"] = wal_enabled
            return report

    def current_revision(self) -> int:
        with closing(self._connect(write=True)) as connection:
            ensure_history_schema(connection)
            return current_revision(connection)

    def put_fact(
        self,
        *,
        namespace: str,
        fact_id: str,
        value: Any,
        actor_ref: str,
        reason_code: str,
        approval_ref: str,
        transaction_ref: str,
        event_type: str | None = None,
        timestamp: str | None = None,
    ) -> dict[str, object]:
        value_json = canonical_json(value)
        value_hash = content_hash(value)
        event_timestamp = timestamp or utc_now()
        with closing(self._connect(write=True)) as connection:
            ensure_history_schema(connection)
            with connection:
                verify_existing_integrity_or_raise(connection)
                namespace = safe_token(namespace, "namespace")
                fact_id = safe_token(fact_id, "fact_id")
                existing = _active_or_tombstoned_fact(connection, namespace, fact_id)
                previous_json = str(existing["value_json"]) if existing is not None else None
                previous_hash = str(existing["value_hash"]) if existing is not None else ZERO_HASH
                resolved_event_type = safe_event_type(
                    event_type or ("create" if existing is None else "update")
                )
                revision = next_revision(connection, timestamp=event_timestamp)
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO private_memory_facts (
                            namespace, fact_id, value_json, value_hash, created_at,
                            updated_at, canonical_revision, tombstoned_at, tombstone_reason
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                        """,
                        (
                            namespace,
                            fact_id,
                            value_json,
                            value_hash,
                            event_timestamp,
                            event_timestamp,
                            revision,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE private_memory_facts
                        SET value_json = ?, value_hash = ?, updated_at = ?,
                            canonical_revision = ?, tombstoned_at = NULL,
                            tombstone_reason = NULL
                        WHERE namespace = ? AND fact_id = ?
                        """,
                        (value_json, value_hash, event_timestamp, revision, namespace, fact_id),
                    )
                event = append_history_event(
                    connection,
                    event_type=resolved_event_type,
                    namespace=namespace,
                    fact_id=fact_id,
                    actor_ref=actor_ref,
                    reason_code=reason_code,
                    approval_ref=approval_ref,
                    transaction_ref=transaction_ref,
                    timestamp=event_timestamp,
                    previous_hash=previous_hash,
                    new_hash=value_hash,
                    canonical_revision=revision,
                    previous_value_json=previous_json,
                    new_value_json=value_json,
                )
                verify_existing_integrity_or_raise(connection)
                return asdict(event)

    def tombstone_fact(
        self,
        *,
        namespace: str,
        fact_id: str,
        actor_ref: str,
        reason_code: str,
        approval_ref: str,
        transaction_ref: str,
        event_type: str = "delete",
        timestamp: str | None = None,
    ) -> dict[str, object]:
        event_timestamp = timestamp or utc_now()
        with closing(self._connect(write=True)) as connection:
            ensure_history_schema(connection)
            with connection:
                verify_existing_integrity_or_raise(connection)
                namespace = safe_token(namespace, "namespace")
                fact_id = safe_token(fact_id, "fact_id")
                existing = _active_or_tombstoned_fact(connection, namespace, fact_id)
                if existing is None or existing["tombstoned_at"] is not None:
                    raise PrivateMemoryWriteError("active fact required for tombstone")
                previous_json = str(existing["value_json"])
                previous_hash = str(existing["value_hash"])
                revision = next_revision(connection, timestamp=event_timestamp)
                connection.execute(
                    """
                    UPDATE private_memory_facts
                    SET updated_at = ?, canonical_revision = ?, tombstoned_at = ?,
                        tombstone_reason = ?
                    WHERE namespace = ? AND fact_id = ?
                    """,
                    (event_timestamp, revision, event_timestamp, reason_code, namespace, fact_id),
                )
                event = append_history_event(
                    connection,
                    event_type=safe_event_type(event_type),
                    namespace=namespace,
                    fact_id=fact_id,
                    actor_ref=actor_ref,
                    reason_code=reason_code,
                    approval_ref=approval_ref,
                    transaction_ref=transaction_ref,
                    timestamp=event_timestamp,
                    previous_hash=previous_hash,
                    new_hash=ZERO_HASH,
                    canonical_revision=revision,
                    previous_value_json=previous_json,
                    new_value_json=None,
                )
                verify_existing_integrity_or_raise(connection)
                return asdict(event)

    def bulk_put_facts(
        self,
        facts: list[Mapping[str, Any]],
        *,
        actor_ref: str,
        reason_code: str,
        approval_ref: str,
        transaction_ref: str,
        pre_operation_snapshot: Mapping[str, object] | None = None,
    ) -> list[dict[str, object]]:
        validated_facts = _validate_bulk_facts(facts)
        actor_ref = safe_token(actor_ref, "actor_ref")
        reason_code = safe_token(reason_code, "reason_code")
        approval_ref = safe_token(approval_ref, "approval_ref")
        transaction_ref = safe_token(transaction_ref, "transaction_ref")
        safe_event_type("supersede")
        events: list[dict[str, object]] = []
        event_timestamp = utc_now()
        with closing(self._connect(write=True)) as connection:
            ensure_history_schema(connection)
            try:
                connection.execute("BEGIN IMMEDIATE")
                verify_existing_integrity_or_raise(connection)
                _verify_pre_operation_snapshot(connection, pre_operation_snapshot)
                for fact in validated_facts:
                    existing = _active_or_tombstoned_fact(connection, fact.namespace, fact.fact_id)
                    previous_json = str(existing["value_json"]) if existing is not None else None
                    previous_hash = str(existing["value_hash"]) if existing is not None else ZERO_HASH
                    revision = next_revision(connection, timestamp=event_timestamp)
                    if existing is None:
                        connection.execute(
                            """
                            INSERT INTO private_memory_facts (
                                namespace, fact_id, value_json, value_hash, created_at,
                                updated_at, canonical_revision, tombstoned_at, tombstone_reason
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                            """,
                            (
                                fact.namespace,
                                fact.fact_id,
                                fact.value_json,
                                fact.value_hash,
                                event_timestamp,
                                event_timestamp,
                                revision,
                            ),
                        )
                    else:
                        connection.execute(
                            """
                            UPDATE private_memory_facts
                            SET value_json = ?, value_hash = ?, updated_at = ?,
                                canonical_revision = ?, tombstoned_at = NULL,
                                tombstone_reason = NULL
                            WHERE namespace = ? AND fact_id = ?
                            """,
                            (
                                fact.value_json,
                                fact.value_hash,
                                event_timestamp,
                                revision,
                                fact.namespace,
                                fact.fact_id,
                            ),
                        )
                    event = append_history_event(
                        connection,
                        event_type="supersede",
                        namespace=fact.namespace,
                        fact_id=fact.fact_id,
                        actor_ref=actor_ref,
                        reason_code=reason_code,
                        approval_ref=approval_ref,
                        transaction_ref=transaction_ref,
                        timestamp=event_timestamp,
                        previous_hash=previous_hash,
                        new_hash=fact.value_hash,
                        canonical_revision=revision,
                        previous_value_json=previous_json,
                        new_value_json=fact.value_json,
                    )
                    events.append(asdict(event))
                verify_existing_integrity_or_raise(connection)
                if len(events) != len(validated_facts):
                    raise PrivateMemoryWriteError("bulk operation incomplete")
                if (
                    validated_facts
                    and events[-1]["canonical_revision"] - events[0]["canonical_revision"] + 1
                    != len(validated_facts)
                ):
                    raise PrivateMemoryWriteError("bulk operation revision gap")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return events

    def get_active_fact(self, *, namespace: str, fact_id: str) -> Any | None:
        with closing(self._connect(write=True)) as connection:
            ensure_history_schema(connection)
            row = connection.execute(
                """
                SELECT value_json FROM private_memory_facts
                WHERE namespace = ? AND fact_id = ? AND tombstoned_at IS NULL
                """,
                (safe_token(namespace, "namespace"), safe_token(fact_id, "fact_id")),
            ).fetchone()
            if row is None:
                return None
            return json.loads(str(row["value_json"]))

    def history(self, *, namespace: str, fact_id: str) -> list[dict[str, object]]:
        with closing(self._connect(write=True)) as connection:
            ensure_history_schema(connection)
            rows = connection.execute(
                """
                SELECT event_type, namespace, fact_id, previous_value_json, new_value_json,
                    previous_hash, new_hash, canonical_revision, timestamp
                FROM private_memory_fact_history
                WHERE namespace = ? AND fact_id = ?
                ORDER BY canonical_revision
                """,
                (safe_token(namespace, "namespace"), safe_token(fact_id, "fact_id")),
            ).fetchall()
            return [
                {
                    "event_type": str(row["event_type"]),
                    "namespace": str(row["namespace"]),
                    "fact_id": str(row["fact_id"]),
                    "previous_value": _json_or_none(row["previous_value_json"]),
                    "new_value": _json_or_none(row["new_value_json"]),
                    "previous_hash": str(row["previous_hash"]),
                    "new_hash": str(row["new_hash"]),
                    "canonical_revision": int(row["canonical_revision"]),
                    "timestamp": str(row["timestamp"]),
                }
                for row in rows
            ]

    def integrity_report(self) -> dict[str, object]:
        with closing(self._connect(write=True)) as connection:
            ensure_history_schema(connection)
            return sanitized_integrity_report(connection)

    def _connect(self, *, write: bool) -> sqlite3.Connection:
        if write:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(str(self.db_path))
        else:
            if not self.db_path.is_file():
                raise PrivateMemoryConfigError("database not found")
            connection = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection


class PrivateMemoryConnector:
    """Server-local SQLite connector with public-safe reporting only."""

    def __init__(
        self,
        config_path: str | Path | None = None,
        *,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.config_path = Path(config_path) if config_path is not None else None
        self.env = env if env is not None else os.environ

    def healthcheck(self, *, request_write: bool = False) -> dict[str, object]:
        try:
            db_path = self._load_db_path()
            if request_write:
                with closing(self._connect(db_path, write=True)) as connection:
                    self._ensure_schema(connection)
                    return self._report_from_connection(
                        connection,
                        status="DONE",
                        writable_when_requested=True,
                        heartbeat_ok=True,
                        next_operator_action="none",
                    )

            with closing(self._connect(db_path, write=False)) as connection:
                return self._report_from_connection(
                    connection,
                    status="DONE",
                    writable_when_requested=False,
                    heartbeat_ok=self._heartbeat_present(connection),
                    next_operator_action=self._schema_action(connection),
                )
        except Exception as exc:  # noqa: BLE001 - public reports must fail closed.
            return _blocked_report(type(exc).__name__)

    def write_heartbeat(self, *, heartbeat_id: str, source: str = "synthetic") -> dict[str, object]:
        try:
            heartbeat_id = _safe_id(heartbeat_id, "heartbeat_id")
            source = _safe_text(source, "source")
            db_path = self._load_db_path()
            with closing(self._connect(db_path, write=True)) as connection:
                self._ensure_schema(connection)
                now = _utc_now()
                with connection:
                    connection.execute(
                        f"""
                        INSERT INTO {_HEARTBEAT_TABLE} (heartbeat_id, source, created_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(heartbeat_id) DO UPDATE SET
                            source = excluded.source,
                            created_at = excluded.created_at
                        """,
                        (heartbeat_id, source, now),
                    )
                heartbeat_ok = self._heartbeat_exists(connection, heartbeat_id)
                if not heartbeat_ok:
                    raise PrivateMemoryWriteError("heartbeat verification failed")
                return self._report_from_connection(
                    connection,
                    status="DONE",
                    writable_when_requested=True,
                    heartbeat_ok=True,
                    next_operator_action="none",
                )
        except Exception as exc:  # noqa: BLE001 - public reports must fail closed.
            return _blocked_report(type(exc).__name__)

    def read_heartbeat(
        self,
        *,
        heartbeat_id: str,
    ) -> dict[str, object]:
        try:
            heartbeat_id = _safe_id(heartbeat_id, "heartbeat_id")
            db_path = self._load_db_path()
            with closing(self._connect(db_path, write=False)) as connection:
                return self._report_from_connection(
                    connection,
                    status="DONE",
                    writable_when_requested=False,
                    heartbeat_ok=self._heartbeat_exists(connection, heartbeat_id),
                    next_operator_action=self._schema_action(connection),
                )
        except Exception as exc:  # noqa: BLE001 - public reports must fail closed.
            return _blocked_report(type(exc).__name__)

    def record_task_state_heartbeat(
        self,
        *,
        task_id: str,
        state: str,
        source: str = "synthetic",
    ) -> dict[str, object]:
        try:
            task_id = _safe_id(task_id, "task_id")
            state = _safe_text(state, "state")
            source = _safe_text(source, "source")
            db_path = self._load_db_path()
            with closing(self._connect(db_path, write=True)) as connection:
                self._ensure_schema(connection)
                now = _utc_now()
                with connection:
                    connection.execute(
                        f"""
                        INSERT INTO {_TASK_STATE_TABLE} (task_id, state, source, updated_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(task_id) DO UPDATE SET
                            state = excluded.state,
                            source = excluded.source,
                            updated_at = excluded.updated_at
                        """,
                        (task_id, state, source, now),
                    )
                if not self._task_state_exists(connection, task_id):
                    raise PrivateMemoryWriteError("task-state heartbeat verification failed")
                return self._report_from_connection(
                    connection,
                    status="DONE",
                    writable_when_requested=True,
                    heartbeat_ok=True,
                    next_operator_action="none",
                )
        except Exception as exc:  # noqa: BLE001 - public reports must fail closed.
            return _blocked_report(type(exc).__name__)

    def _load_db_path(self) -> Path:
        config_path = self.config_path
        if config_path is None:
            configured = self.env.get(PRIVATE_MEMORY_CONFIG_ENV)
            if not configured:
                raise PrivateMemoryConfigError("missing config")
            config_path = Path(configured)
        if not config_path.is_file():
            raise PrivateMemoryConfigError("config not found")

        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PrivateMemoryConfigError("invalid config json") from exc
        if not isinstance(config, Mapping):
            raise PrivateMemoryConfigError("invalid config object")

        schema = config.get("schema")
        if schema == PRIVATE_MEMORY_CONFIG_SCHEMA:
            return self._load_explicit_config_db_path(config, config_path)
        if schema in PRIVATE_MEMORY_BOOTSTRAP_REGISTRY_SCHEMAS:
            return self._load_bootstrap_registry_db_path(config, config_path)
        raise PrivateMemoryConfigError("invalid config schema")

    def _load_explicit_config_db_path(self, config: Mapping[str, Any], config_path: Path) -> Path:
        database = config.get("database")
        if not isinstance(database, Mapping):
            raise PrivateMemoryConfigError("missing database config")
        raw_path = database.get("path")
        return _normalize_db_path(raw_path, base_path=config_path.parent)

    def _load_bootstrap_registry_db_path(self, registry: Mapping[str, Any], registry_path: Path) -> Path:
        root_path = _optional_path_value(registry.get("root_path"), registry_path.parent)
        if root_path is None:
            root_path = _optional_path_value(registry.get("root"), registry_path.parent)
        base_path = root_path if root_path is not None else registry_path.parent

        memory_config = _first_mapping(
            registry.get("private_memory"),
            _nested_mapping(registry, ("connectors", "private_memory")),
            _nested_mapping(registry, ("services", "private_memory")),
            _nested_mapping(registry, ("memory", "private_memory")),
            _nested_mapping(registry, ("memory", "private_sqlite")),
        )
        if memory_config is None:
            raise PrivateMemoryConfigError("missing private memory registry config")

        sqlite_config = _first_mapping(
            memory_config.get("sqlite"),
            memory_config.get("database"),
            memory_config.get("db"),
            memory_config,
        )
        if sqlite_config is None:
            raise PrivateMemoryConfigError("missing sqlite registry config")

        raw_path = _first_present(
            sqlite_config.get("path"),
            sqlite_config.get("db_path"),
            sqlite_config.get("database_path"),
            sqlite_config.get("sqlite_path"),
        )
        return _normalize_db_path(raw_path, base_path=base_path)

    def _connect(self, db_path: Path, *, write: bool) -> sqlite3.Connection:
        if not write and not db_path.is_file():
            raise PrivateMemoryConfigError("database not found")
        if write:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(str(db_path))
        else:
            uri = f"file:{db_path.as_posix()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {_HEARTBEAT_TABLE} (
                heartbeat_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS {_TASK_STATE_TABLE} (
                task_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        connection.commit()

    def _report_from_connection(
        self,
        connection: sqlite3.Connection,
        *,
        status: str,
        writable_when_requested: bool,
        heartbeat_ok: bool,
        next_operator_action: str,
    ) -> dict[str, object]:
        integrity_ok = _integrity_ok(connection)
        if not integrity_ok:
            raise PrivateMemoryIntegrityError("integrity check failed")
        schema_present, table_count = self._schema_status(connection)
        report = PrivateMemoryHealthcheck(
            schema=PRIVATE_MEMORY_HEALTHCHECK_SCHEMA,
            status=status if schema_present or status == "BLOCKED" else "BLOCKED",
            db_configured=True,
            db_openable=True,
            integrity_ok=integrity_ok,
            schema_present=schema_present,
            table_count=table_count,
            writable_when_requested=writable_when_requested,
            heartbeat_ok=heartbeat_ok,
            error_class=None if schema_present else "PrivateMemorySchemaError",
            next_operator_action=next_operator_action if schema_present else "initialize_private_memory_schema",
        )
        return _sanitize_report(report)

    def _schema_status(self, connection: sqlite3.Connection) -> tuple[bool, int]:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        names = {str(row["name"]) for row in rows}
        return _CONNECTOR_TABLES.issubset(names), len(names)

    def _schema_action(self, connection: sqlite3.Connection) -> str:
        schema_present, _table_count = self._schema_status(connection)
        if schema_present:
            return "none"
        return "initialize_private_memory_schema"

    def _heartbeat_present(self, connection: sqlite3.Connection) -> bool:
        schema_present, _table_count = self._schema_status(connection)
        if not schema_present:
            return False
        row = connection.execute(f"SELECT 1 FROM {_HEARTBEAT_TABLE} LIMIT 1").fetchone()
        return row is not None

    def _heartbeat_exists(self, connection: sqlite3.Connection, heartbeat_id: str) -> bool:
        schema_present, _table_count = self._schema_status(connection)
        if not schema_present:
            return False
        row = connection.execute(
            f"SELECT 1 FROM {_HEARTBEAT_TABLE} WHERE heartbeat_id = ?",
            (heartbeat_id,),
        ).fetchone()
        return row is not None

    def _task_state_exists(self, connection: sqlite3.Connection, task_id: str) -> bool:
        schema_present, _table_count = self._schema_status(connection)
        if not schema_present:
            return False
        row = connection.execute(
            f"SELECT 1 FROM {_TASK_STATE_TABLE} WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return row is not None


class PrivateMemoryError(Exception):
    """Base exception for private memory connector setup failures."""


class PrivateMemoryConfigError(PrivateMemoryError):
    """Raised when the local config is missing or invalid."""


class PrivateMemoryIntegrityError(PrivateMemoryError):
    """Raised when SQLite integrity checks fail."""


class PrivateMemorySchemaError(PrivateMemoryError):
    """Raised when the expected connector schema is unavailable."""


class PrivateMemoryPrivacyError(PrivateMemoryError):
    """Raised when a public report would expose private details."""


class PrivateMemoryWriteError(PrivateMemoryError):
    """Raised when an explicit write request cannot be verified."""


def _blocked_report(error_class: str) -> dict[str, object]:
    report = PrivateMemoryHealthcheck(
        schema=PRIVATE_MEMORY_HEALTHCHECK_SCHEMA,
        status="BLOCKED",
        db_configured=False,
        db_openable=False,
        integrity_ok=False,
        schema_present=False,
        table_count=0,
        writable_when_requested=False,
        heartbeat_ok=False,
        error_class=error_class,
        next_operator_action="configure_private_memory",
    )
    return _sanitize_report(report)


def _sanitize_report(report: PrivateMemoryHealthcheck) -> dict[str, object]:
    data = asdict(report)
    for key, value in data.items():
        lowered = key.lower()
        if lowered in _UNSAFE_REPORT_KEYS:
            raise PrivateMemoryPrivacyError("unsafe report key")
        if isinstance(value, str) and _looks_private(value):
            raise PrivateMemoryPrivacyError("unsafe report value")
    return data


def _integrity_ok(connection: sqlite3.Connection) -> bool:
    row = connection.execute("PRAGMA integrity_check").fetchone()
    return row is not None and row[0] == "ok"


def _normalize_db_path(raw_path: object, *, base_path: Path) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise PrivateMemoryConfigError("missing database path")
    if raw_path == ":memory:":
        raise PrivateMemoryConfigError("persistent local database path required")
    db_path = Path(raw_path)
    if not db_path.is_absolute():
        db_path = base_path / db_path
    return db_path


def _optional_path_value(raw_path: object, base_path: Path) -> Path | None:
    if raw_path is None:
        return None
    if not isinstance(raw_path, str) or not raw_path:
        raise PrivateMemoryConfigError("invalid registry path")
    path = Path(raw_path)
    if not path.is_absolute():
        path = base_path / path
    return path


def _first_mapping(*values: object) -> Mapping[str, Any] | None:
    for value in values:
        if isinstance(value, Mapping):
            return value
    return None


def _nested_mapping(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> Mapping[str, Any] | None:
    current: object = mapping
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    if isinstance(current, Mapping):
        return current
    return None


def _first_present(*values: object) -> object:
    for value in values:
        if value is not None:
            return value
    return None


def _active_or_tombstoned_fact(
    connection: sqlite3.Connection, namespace: str, fact_id: str
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT value_json, value_hash, tombstoned_at
        FROM private_memory_facts
        WHERE namespace = ? AND fact_id = ?
        """,
        (namespace, fact_id),
    ).fetchone()


def _validate_bulk_facts(facts: object) -> list[_ValidatedBulkFact]:
    if not isinstance(facts, Sequence) or isinstance(facts, (str, bytes)):
        raise PrivateMemoryWriteError("bulk facts must be a sequence")
    validated: list[_ValidatedBulkFact] = []
    for fact in facts:
        if not isinstance(fact, Mapping):
            raise PrivateMemoryWriteError("bulk fact must be an object")
        try:
            namespace = safe_token(fact["namespace"], "namespace")
            fact_id = safe_token(fact["fact_id"], "fact_id")
            value = fact["value"]
        except KeyError as exc:
            raise PrivateMemoryWriteError("bulk fact missing required field") from exc
        value_json = canonical_json(value)
        validated.append(
            _ValidatedBulkFact(
                namespace=namespace,
                fact_id=fact_id,
                value_json=value_json,
                value_hash=content_hash(value),
            )
        )
    return validated


def _verify_pre_operation_snapshot(
    connection: sqlite3.Connection,
    pre_operation_snapshot: Mapping[str, object] | None,
) -> None:
    if not isinstance(pre_operation_snapshot, Mapping):
        raise PrivateMemoryWriteError("bulk operation requires pre-operation snapshot artifact")
    manifest = pre_operation_snapshot.get("manifest")
    snapshot_path = _snapshot_artifact_path(pre_operation_snapshot)
    if not isinstance(manifest, Mapping) or snapshot_path is None:
        raise PrivateMemoryWriteError("bulk operation requires snapshot artifact and manifest")
    if not snapshot_path.is_file():
        raise PrivateMemoryWriteError("bulk operation snapshot artifact unavailable")
    if bytes_hash(snapshot_path.read_bytes()) != _manifest_content_hash(manifest):
        raise PrivateMemoryWriteError("bulk operation snapshot proof hash mismatch")

    with sqlite3.connect(f"file:{snapshot_path.as_posix()}?mode=ro", uri=True) as snapshot:
        snapshot.row_factory = sqlite3.Row
        _verify_snapshot_manifest_fields(snapshot, manifest)
        verify_existing_integrity_or_raise(connection)
        if _aggregate_counts(connection) != manifest["aggregate_counts"]:
            raise PrivateMemoryWriteError("bulk operation snapshot proof is stale")
        if current_revision(connection) != manifest["canonical_revision"]:
            raise PrivateMemoryWriteError("bulk operation snapshot proof revision mismatch")
        if canonical_logical_state_digest(snapshot) != canonical_logical_state_digest(connection):
            raise PrivateMemoryWriteError("bulk operation snapshot proof database mismatch")


def _snapshot_artifact_path(pre_operation_snapshot: Mapping[str, object]) -> Path | None:
    raw_path = (
        pre_operation_snapshot.get("snapshot_path")
        or pre_operation_snapshot.get("snapshot_file_path")
        or pre_operation_snapshot.get("artifact_path")
    )
    if not isinstance(raw_path, (str, Path)):
        return None
    return Path(raw_path)


def _manifest_content_hash(manifest: Mapping[str, object]) -> str:
    if manifest.get("schema") != PRIVATE_MEMORY_SNAPSHOT_MANIFEST_SCHEMA:
        raise PrivateMemoryWriteError("invalid pre-operation snapshot manifest schema")
    content_hash_value = manifest.get("content_hash")
    if not isinstance(content_hash_value, str) or not re.fullmatch(r"[a-f0-9]{64}", content_hash_value):
        raise PrivateMemoryWriteError("invalid pre-operation snapshot hash")
    return content_hash_value


def _verify_snapshot_manifest_fields(
    snapshot: sqlite3.Connection,
    manifest: Mapping[str, object],
) -> None:
    _manifest_content_hash(manifest)
    state_hash = manifest.get("canonical_state_hash")
    if not isinstance(state_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", state_hash):
        raise PrivateMemoryWriteError("invalid pre-operation snapshot state hash")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise PrivateMemoryWriteError("invalid pre-operation snapshot schema version")
    if current_revision(snapshot) != manifest.get("canonical_revision"):
        raise PrivateMemoryWriteError("pre-operation snapshot revision mismatch")
    if _database_schema_version(snapshot) != SCHEMA_VERSION:
        raise PrivateMemoryWriteError("pre-operation snapshot database schema mismatch")
    if _aggregate_counts(snapshot) != manifest.get("aggregate_counts"):
        raise PrivateMemoryWriteError("pre-operation snapshot aggregate mismatch")
    if canonical_logical_state_digest(snapshot) != state_hash:
        raise PrivateMemoryWriteError("pre-operation snapshot state mismatch")


def _database_schema_version(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT value FROM private_memory_meta WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        raise PrivateMemoryWriteError("missing private memory schema version")
    return str(row[0])


def _aggregate_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        "facts": _table_count(connection, "private_memory_facts"),
        "events": _table_count(connection, "private_memory_events"),
        "history_entries": _table_count(connection, "private_memory_fact_history"),
        "tombstones": _table_count(connection, "private_memory_tombstones"),
    }


def _table_count(connection: sqlite3.Connection, table_name: str) -> int:
    if table_name not in {
        "private_memory_facts",
        "private_memory_events",
        "private_memory_fact_history",
        "private_memory_tombstones",
    }:
        raise PrivateMemoryWriteError("unsupported aggregate table")
    return int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def _json_or_none(value: object) -> Any | None:
    if value is None:
        return None
    return json.loads(str(value))


def _safe_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
        raise ValueError(f"{name} must be a synthetic public-safe id")
    return value


def _safe_text(value: str, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_TEXT_RE.fullmatch(value):
        raise ValueError(f"{name} must be synthetic public-safe text")
    lowered = value.lower()
    if any(marker in lowered for marker in ("secret", "token", "password", "credential")):
        raise ValueError(f"{name} must not contain unsafe markers")
    return value


def _looks_private(value: str) -> bool:
    lowered = value.lower()
    return (
        "/" in value
        or "\\" in value
        or "file:" in lowered
        or ".sqlite" in lowered
        or ".db" in lowered
        or "token" in lowered
        or "secret" in lowered
        or "password" in lowered
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
