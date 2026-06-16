from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


PRIVATE_MEMORY_CONFIG_ENV = "SKELETON_PRIVATE_MEMORY_CONFIG"
PRIVATE_MEMORY_CONFIG_SCHEMA = "skeleton.private_memory.config.v0"
PRIVATE_MEMORY_HEALTHCHECK_SCHEMA = "skeleton.private_memory.healthcheck.v0"

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
        if config.get("schema") != PRIVATE_MEMORY_CONFIG_SCHEMA:
            raise PrivateMemoryConfigError("invalid config schema")
        database = config.get("database")
        if not isinstance(database, Mapping):
            raise PrivateMemoryConfigError("missing database config")
        raw_path = database.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise PrivateMemoryConfigError("missing database path")
        if raw_path == ":memory:":
            raise PrivateMemoryConfigError("persistent local database path required")
        db_path = Path(raw_path)
        if not db_path.is_absolute():
            db_path = config_path.parent / db_path
        return db_path

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
