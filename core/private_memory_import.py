from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.private_memory import (
    PRIVATE_MEMORY_BOOTSTRAP_REGISTRY_SCHEMAS,
    PRIVATE_MEMORY_CONFIG_ENV,
    PRIVATE_MEMORY_CONFIG_SCHEMA,
    PrivateMemoryConnector,
)


PRIVATE_MEMORY_SEED_IMPORT_REPORT_SCHEMA = "skeleton.private_memory_seed_import.report.v1"
PRIVATE_MEMORY_SEED_MANIFEST_SCHEMA = "skeleton.private_memory_seed.manifest.v1"
PRIVATE_MEMORY_SEED_CONFIG_ENV = "SKELETON_PRIVATE_MEMORY_SEED_CONFIG"
PRIVATE_MEMORY_SEED_WRITE_GATE = "private_memory_seed_import_v1"

_ALLOWED_ZIP_MEMBERS = frozenset({"manifest.json", "records.sqlite"})
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_SQLITE_BYTES = 2 * 1024 * 1024
_MAX_RECORDS = 1000
_MAX_STATUS_ROWS = 5000
_MAX_TEXT_BYTES = 16 * 1024
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_STATUS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.: -]{0,127}$")
_ALLOWED_PAYLOAD_CLASSES = frozenset({"note", "task", "project_fact"})
_UNSAFE_REPORT_KEYS = frozenset({"path", "payload", "content", "locator", "source_locator"})
_UNSAFE_REPORT_VALUE_RE = re.compile(
    r"(?i)(/|\\|file:|\.sqlite\b|\.db\b|secret|token|password|credential|select\s|create\s+table)"
)


@dataclass(frozen=True)
class PrivateMemorySeedImportReport:
    schema: str
    status: str
    write_gate_open: bool
    package_present: bool
    archive_valid: bool
    manifest_valid: bool
    checksum_valid: bool
    sqlite_valid: bool
    snapshot_created: bool
    transaction_committed: bool
    batch_count: int
    imported_record_count: int
    status_history_count: int
    audit_record_count: int
    canonical_record_count: int
    idempotent: bool
    error_class: str | None
    next_operator_action: str


def import_private_memory_seed(
    *,
    write_enabled: bool = False,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Import an operator-staged local seed package with public-safe reporting."""
    importer = PrivateMemorySeedImporter(config_path=config_path, env=env)
    return importer.import_seed(write_enabled=write_enabled)


class PrivateMemorySeedImporter:
    def __init__(
        self,
        config_path: str | Path | None = None,
        *,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.config_path = Path(config_path) if config_path is not None else None
        self.env = env if env is not None else os.environ

    def import_seed(self, *, write_enabled: bool = False) -> dict[str, object]:
        if not write_enabled:
            return _blocked_report(
                "PrivateMemorySeedImportWriteGateError",
                write_gate_open=False,
                next_operator_action="operator_enable_private_memory_seed_import",
            )

        try:
            config_path = self._resolve_config_path()
            db_path = PrivateMemoryConnector(config_path=config_path, env=self.env)._load_db_path()
            package_path = self._load_seed_package_path(config_path)
            manifest, sqlite_bytes, package_sha256 = _read_validated_package(package_path)
            seed_records, status_rows = _read_seed_sqlite(sqlite_bytes, manifest)
            with closing(_connect_writable(db_path)) as connection:
                _create_sqlite_snapshot(connection)
                imported_record_count = 0
                status_history_count = 0
                audit_record_count = 0
                idempotent = False
                now = _utc_now()
                try:
                    connection.execute("BEGIN")
                    _ensure_import_schema(connection)
                    if _batch_completed(connection, package_sha256):
                        idempotent = True
                    else:
                        _write_import_batch(
                            connection,
                            package_sha256=package_sha256,
                            manifest=manifest,
                            now=now,
                        )
                        imported_record_count = _write_records(
                            connection,
                            package_sha256=package_sha256,
                            records=seed_records,
                            now=now,
                        )
                        status_history_count = _write_status_history(
                            connection,
                            package_sha256=package_sha256,
                            rows=status_rows,
                        )
                        audit_record_count = _write_audit_records(
                            connection,
                            package_sha256=package_sha256,
                            record_count=imported_record_count,
                            status_history_count=status_history_count,
                            now=now,
                        )
                        connection.execute(
                            """
                            UPDATE private_memory_import_batches
                            SET status = ?, completed_at = ?
                            WHERE package_sha256 = ?
                            """,
                            ("completed", now, package_sha256),
                        )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise

                return _done_report(
                    connection,
                    write_gate_open=True,
                    package_present=True,
                    archive_valid=True,
                    manifest_valid=True,
                    checksum_valid=True,
                    sqlite_valid=True,
                    snapshot_created=not idempotent,
                    transaction_committed=not idempotent,
                    imported_record_count=imported_record_count,
                    status_history_count=status_history_count,
                    audit_record_count=audit_record_count,
                    idempotent=idempotent,
                )
        except Exception as exc:  # noqa: BLE001 - public reports fail closed.
            return _blocked_report(type(exc).__name__, write_gate_open=True)

    def _resolve_config_path(self) -> Path:
        if self.config_path is not None:
            return self.config_path
        raw_path = self.env.get(PRIVATE_MEMORY_SEED_CONFIG_ENV) or self.env.get(
            PRIVATE_MEMORY_CONFIG_ENV
        )
        if not raw_path:
            raise PrivateMemorySeedConfigError("missing config")
        return Path(raw_path)

    def _load_seed_package_path(self, config_path: Path) -> Path:
        config = _load_json_mapping(config_path)
        schema = config.get("schema")
        if schema == PRIVATE_MEMORY_CONFIG_SCHEMA:
            seed_config = _first_mapping(
                config.get("seed_package"),
                config.get("private_memory_seed"),
                config.get("seed"),
            )
            base_path = config_path.parent
        elif schema in PRIVATE_MEMORY_BOOTSTRAP_REGISTRY_SCHEMAS:
            root = _optional_path(config.get("root_path"), config_path.parent)
            if root is None:
                root = _optional_path(config.get("root"), config_path.parent)
            base_path = root or config_path.parent
            memory_config = _first_mapping(
                config.get("private_memory"),
                _nested_mapping(config, ("connectors", "private_memory")),
                _nested_mapping(config, ("services", "private_memory")),
                _nested_mapping(config, ("memory", "private_memory")),
                _nested_mapping(config, ("memory", "private_sqlite")),
            )
            seed_config = (
                _first_mapping(
                    memory_config.get("seed_package") if memory_config else None,
                    memory_config.get("private_memory_seed") if memory_config else None,
                    memory_config.get("seed") if memory_config else None,
                )
                if memory_config
                else None
            )
        else:
            raise PrivateMemorySeedConfigError("invalid config schema")

        if seed_config is None:
            raise PrivateMemorySeedConfigError("missing seed package config")
        raw_path = _first_present(
            seed_config.get("path"),
            seed_config.get("zip_path"),
            seed_config.get("package_path"),
        )
        if not isinstance(raw_path, str) or not raw_path:
            raise PrivateMemorySeedConfigError("missing seed package path")
        package_path = Path(raw_path)
        if not package_path.is_absolute():
            package_path = base_path / package_path
        if not package_path.is_file():
            raise PrivateMemorySeedConfigError("seed package not found")
        return package_path


class PrivateMemorySeedImportError(Exception):
    """Base exception for bounded private memory seed import failures."""


class PrivateMemorySeedConfigError(PrivateMemorySeedImportError):
    """Raised when local config or registry staging metadata is invalid."""


class PrivateMemorySeedArchiveError(PrivateMemorySeedImportError):
    """Raised when the staged archive structure is not allowlisted."""


class PrivateMemorySeedManifestError(PrivateMemorySeedImportError):
    """Raised when the seed manifest is invalid."""


class PrivateMemorySeedChecksumError(PrivateMemorySeedImportError):
    """Raised when staged payload checksums do not match the manifest."""


class PrivateMemorySeedSqliteError(PrivateMemorySeedImportError):
    """Raised when the staged seed SQLite is invalid."""


def _read_validated_package(package_path: Path) -> tuple[Mapping[str, Any], bytes, str]:
    package_bytes = package_path.read_bytes()
    package_sha256 = hashlib.sha256(package_bytes).hexdigest()
    try:
        with zipfile.ZipFile(package_path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if sorted(names) != sorted(_ALLOWED_ZIP_MEMBERS) or len(names) != len(set(names)):
                raise PrivateMemorySeedArchiveError("archive members are not allowlisted")
            for info in infos:
                _validate_member(info)
            manifest_bytes = archive.read("manifest.json")
            sqlite_bytes = archive.read("records.sqlite")
    except zipfile.BadZipFile as exc:
        raise PrivateMemorySeedArchiveError("invalid zip") from exc

    manifest = _parse_manifest(manifest_bytes)
    checksums = manifest.get("checksums")
    if not isinstance(checksums, Mapping):
        raise PrivateMemorySeedManifestError("missing checksums")
    expected_manifest_checksum = checksums.get("manifest.json")
    expected_sqlite_checksum = checksums.get("records.sqlite")
    if expected_manifest_checksum is not None:
        raise PrivateMemorySeedManifestError("manifest must not checksum itself")
    if not isinstance(expected_sqlite_checksum, str):
        raise PrivateMemorySeedManifestError("missing sqlite checksum")
    if hashlib.sha256(sqlite_bytes).hexdigest() != expected_sqlite_checksum:
        raise PrivateMemorySeedChecksumError("sqlite checksum mismatch")
    return manifest, sqlite_bytes, package_sha256


def _validate_member(info: zipfile.ZipInfo) -> None:
    name = info.filename
    if name not in _ALLOWED_ZIP_MEMBERS or name.startswith("/") or "\\" in name:
        raise PrivateMemorySeedArchiveError("unsafe archive member")
    if ".." in Path(name).parts or name.endswith("/"):
        raise PrivateMemorySeedArchiveError("unsafe archive member")
    if info.file_size < 0 or info.compress_size < 0:
        raise PrivateMemorySeedArchiveError("invalid archive member size")
    if name == "manifest.json" and info.file_size > _MAX_MANIFEST_BYTES:
        raise PrivateMemorySeedArchiveError("manifest too large")
    if name == "records.sqlite" and info.file_size > _MAX_SQLITE_BYTES:
        raise PrivateMemorySeedArchiveError("sqlite too large")


def _parse_manifest(manifest_bytes: bytes) -> Mapping[str, Any]:
    if len(manifest_bytes) > _MAX_MANIFEST_BYTES:
        raise PrivateMemorySeedManifestError("manifest too large")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrivateMemorySeedManifestError("invalid manifest json") from exc
    if not isinstance(manifest, Mapping):
        raise PrivateMemorySeedManifestError("manifest is not an object")
    if manifest.get("schema") != PRIVATE_MEMORY_SEED_MANIFEST_SCHEMA:
        raise PrivateMemorySeedManifestError("invalid manifest schema")
    if manifest.get("manifest_version") != 1:
        raise PrivateMemorySeedManifestError("invalid manifest version")
    max_records = manifest.get("record_count")
    max_status = manifest.get("status_history_count")
    if not isinstance(max_records, int) or max_records < 0 or max_records > _MAX_RECORDS:
        raise PrivateMemorySeedManifestError("invalid record count")
    if not isinstance(max_status, int) or max_status < 0 or max_status > _MAX_STATUS_ROWS:
        raise PrivateMemorySeedManifestError("invalid status count")
    return manifest


def _read_seed_sqlite(
    sqlite_bytes: bytes, manifest: Mapping[str, Any]
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    if len(sqlite_bytes) > _MAX_SQLITE_BYTES:
        raise PrivateMemorySeedSqliteError("seed sqlite too large")
    with tempfile.TemporaryDirectory(prefix="skeleton-seed-") as tmp_dir:
        sqlite_path = Path(tmp_dir) / "records.sqlite"
        sqlite_path.write_bytes(sqlite_bytes)
        uri = f"file:{sqlite_path.as_posix()}?mode=ro"
        try:
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                connection.row_factory = sqlite3.Row
                if not _integrity_ok(connection):
                    raise PrivateMemorySeedSqliteError("seed integrity check failed")
                _validate_seed_schema(connection)
                records = _load_seed_records(connection)
                statuses = _load_seed_status_history(connection)
        except sqlite3.DatabaseError as exc:
            raise PrivateMemorySeedSqliteError("seed sqlite open failed") from exc

    if len(records) != manifest.get("record_count"):
        raise PrivateMemorySeedManifestError("record count mismatch")
    if len(statuses) != manifest.get("status_history_count"):
        raise PrivateMemorySeedManifestError("status count mismatch")
    record_ids = {record["record_id"] for record in records}
    if any(row["record_id"] not in record_ids for row in statuses):
        raise PrivateMemorySeedSqliteError("status references unknown record")
    return records, statuses


def _validate_seed_schema(connection: sqlite3.Connection) -> None:
    expected = {
        "seed_records": {
            "record_id",
            "payload_class",
            "canonical_text",
            "source_locator",
            "created_at",
        },
        "seed_status_history": {"record_id", "status", "changed_at"},
    }
    for table, columns in expected.items():
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if row is None:
            raise PrivateMemorySeedSqliteError("missing seed table")
        actual = {str(info["name"]) for info in connection.execute(f"PRAGMA table_info({table})")}
        if actual != columns:
            raise PrivateMemorySeedSqliteError("unexpected seed schema")


def _load_seed_records(connection: sqlite3.Connection) -> list[dict[str, str]]:
    rows = connection.execute(
        """
        SELECT record_id, payload_class, canonical_text, source_locator, created_at
        FROM seed_records
        ORDER BY record_id
        """
    ).fetchall()
    if len(rows) > _MAX_RECORDS:
        raise PrivateMemorySeedSqliteError("too many records")
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        record = {key: _required_text(row[key], key) for key in row.keys()}
        _validate_seed_record(record)
        if record["record_id"] in seen:
            raise PrivateMemorySeedSqliteError("duplicate record id")
        seen.add(record["record_id"])
        records.append(record)
    return records


def _load_seed_status_history(connection: sqlite3.Connection) -> list[dict[str, str]]:
    rows = connection.execute(
        """
        SELECT record_id, status, changed_at
        FROM seed_status_history
        ORDER BY record_id, changed_at, status
        """
    ).fetchall()
    if len(rows) > _MAX_STATUS_ROWS:
        raise PrivateMemorySeedSqliteError("too many status rows")
    statuses: list[dict[str, str]] = []
    for row in rows:
        status = {key: _required_text(row[key], key) for key in row.keys()}
        if not _SAFE_ID_RE.fullmatch(status["record_id"]):
            raise PrivateMemorySeedSqliteError("unsafe status record id")
        if not _SAFE_STATUS_RE.fullmatch(status["status"]):
            raise PrivateMemorySeedSqliteError("unsafe status")
        _validate_iso_timestamp(status["changed_at"])
        statuses.append(status)
    return statuses


def _validate_seed_record(record: Mapping[str, str]) -> None:
    if not _SAFE_ID_RE.fullmatch(record["record_id"]):
        raise PrivateMemorySeedSqliteError("unsafe record id")
    if record["payload_class"] not in _ALLOWED_PAYLOAD_CLASSES:
        raise PrivateMemorySeedSqliteError("unsafe payload class")
    _validate_text_size(record["canonical_text"], "canonical_text")
    _validate_text_size(record["source_locator"], "source_locator")
    _validate_iso_timestamp(record["created_at"])


def _ensure_import_schema(connection: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS private_memory_import_batches (
            package_sha256 TEXT PRIMARY KEY,
            manifest_schema TEXT NOT NULL,
            manifest_version INTEGER NOT NULL,
            status TEXT NOT NULL,
            record_count INTEGER NOT NULL,
            status_history_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS private_memory_import_records (
            package_sha256 TEXT NOT NULL,
            seed_record_id TEXT NOT NULL,
            payload_class TEXT NOT NULL,
            canonical_text TEXT NOT NULL,
            source_locator TEXT NOT NULL,
            seed_created_at TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            PRIMARY KEY (package_sha256, seed_record_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS private_memory_import_status_history (
            package_sha256 TEXT NOT NULL,
            seed_record_id TEXT NOT NULL,
            status TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            PRIMARY KEY (package_sha256, seed_record_id, status, changed_at)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS private_memory_import_audit (
            package_sha256 TEXT NOT NULL,
            audit_event TEXT NOT NULL,
            event_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (package_sha256, audit_event)
        )
        """,
    )
    for statement in statements:
        connection.execute(statement)


def _write_import_batch(
    connection: sqlite3.Connection,
    *,
    package_sha256: str,
    manifest: Mapping[str, Any],
    now: str,
) -> None:
    connection.execute(
        """
        INSERT INTO private_memory_import_batches (
            package_sha256, manifest_schema, manifest_version, status,
            record_count, status_history_count, created_at, completed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            package_sha256,
            PRIVATE_MEMORY_SEED_MANIFEST_SCHEMA,
            int(manifest["manifest_version"]),
            "importing",
            int(manifest["record_count"]),
            int(manifest["status_history_count"]),
            now,
        ),
    )


def _write_records(
    connection: sqlite3.Connection,
    *,
    package_sha256: str,
    records: Sequence[Mapping[str, str]],
    now: str,
) -> int:
    count = 0
    for record in records:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO private_memory_import_records (
                package_sha256, seed_record_id, payload_class, canonical_text,
                source_locator, seed_created_at, imported_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                package_sha256,
                record["record_id"],
                record["payload_class"],
                record["canonical_text"],
                record["source_locator"],
                record["created_at"],
                now,
            ),
        )
        count += cursor.rowcount
    return count


def _write_status_history(
    connection: sqlite3.Connection,
    *,
    package_sha256: str,
    rows: Sequence[Mapping[str, str]],
) -> int:
    count = 0
    for row in rows:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO private_memory_import_status_history (
                package_sha256, seed_record_id, status, changed_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (package_sha256, row["record_id"], row["status"], row["changed_at"]),
        )
        count += cursor.rowcount
    return count


def _write_audit_records(
    connection: sqlite3.Connection,
    *,
    package_sha256: str,
    record_count: int,
    status_history_count: int,
    now: str,
) -> int:
    events = (
        ("snapshot_created", 1),
        ("records_imported", record_count),
        ("status_history_imported", status_history_count),
    )
    for event, event_count in events:
        connection.execute(
            """
            INSERT OR IGNORE INTO private_memory_import_audit (
                package_sha256, audit_event, event_count, created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (package_sha256, event, event_count, now),
        )
    return len(events)


def _create_sqlite_snapshot(connection: sqlite3.Connection) -> None:
    with tempfile.TemporaryFile() as snapshot_file:
        snapshot_path = f"/proc/self/fd/{snapshot_file.fileno()}"
        with closing(sqlite3.connect(snapshot_path)) as snapshot_connection:
            connection.backup(snapshot_connection)


def _done_report(
    connection: sqlite3.Connection,
    *,
    write_gate_open: bool,
    package_present: bool,
    archive_valid: bool,
    manifest_valid: bool,
    checksum_valid: bool,
    sqlite_valid: bool,
    snapshot_created: bool,
    transaction_committed: bool,
    imported_record_count: int,
    status_history_count: int,
    audit_record_count: int,
    idempotent: bool,
) -> dict[str, object]:
    report = PrivateMemorySeedImportReport(
        schema=PRIVATE_MEMORY_SEED_IMPORT_REPORT_SCHEMA,
        status="DONE",
        write_gate_open=write_gate_open,
        package_present=package_present,
        archive_valid=archive_valid,
        manifest_valid=manifest_valid,
        checksum_valid=checksum_valid,
        sqlite_valid=sqlite_valid,
        snapshot_created=snapshot_created,
        transaction_committed=transaction_committed,
        batch_count=_count(connection, "private_memory_import_batches"),
        imported_record_count=imported_record_count,
        status_history_count=status_history_count,
        audit_record_count=audit_record_count,
        canonical_record_count=_count(connection, "private_memory_import_records"),
        idempotent=idempotent,
        error_class=None,
        next_operator_action="none",
    )
    return _sanitize_report(report)


def _blocked_report(
    error_class: str,
    *,
    write_gate_open: bool,
    next_operator_action: str = "operator_review_private_memory_seed_import",
) -> dict[str, object]:
    report = PrivateMemorySeedImportReport(
        schema=PRIVATE_MEMORY_SEED_IMPORT_REPORT_SCHEMA,
        status="BLOCKED",
        write_gate_open=write_gate_open,
        package_present=False,
        archive_valid=False,
        manifest_valid=False,
        checksum_valid=False,
        sqlite_valid=False,
        snapshot_created=False,
        transaction_committed=False,
        batch_count=0,
        imported_record_count=0,
        status_history_count=0,
        audit_record_count=0,
        canonical_record_count=0,
        idempotent=False,
        error_class=_safe_error_class(error_class),
        next_operator_action=next_operator_action,
    )
    return _sanitize_report(report)


def _sanitize_report(report: PrivateMemorySeedImportReport) -> dict[str, object]:
    data = asdict(report)
    for key, value in data.items():
        lowered = key.lower()
        if lowered in _UNSAFE_REPORT_KEYS:
            raise PrivateMemorySeedImportError("unsafe report key")
        if isinstance(value, str) and _UNSAFE_REPORT_VALUE_RE.search(value):
            raise PrivateMemorySeedImportError("unsafe report value")
    return data


def _connect_writable(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    if not _integrity_ok(connection):
        raise PrivateMemorySeedSqliteError("target integrity check failed")
    return connection


def _integrity_ok(connection: sqlite3.Connection) -> bool:
    row = connection.execute("PRAGMA integrity_check").fetchone()
    return row is not None and row[0] == "ok"


def _batch_completed(connection: sqlite3.Connection, package_sha256: str) -> bool:
    row = connection.execute(
        """
        SELECT 1 FROM private_memory_import_batches
        WHERE package_sha256 = ? AND status = 'completed'
        """,
        (package_sha256,),
    ).fetchone()
    return row is not None


def _count(connection: sqlite3.Connection, table_name: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def _load_json_mapping(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise PrivateMemorySeedConfigError("config not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PrivateMemorySeedConfigError("invalid config json") from exc
    if not isinstance(data, Mapping):
        raise PrivateMemorySeedConfigError("invalid config object")
    return data


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or value == "":
        raise PrivateMemorySeedSqliteError(f"missing {name}")
    return value


def _validate_text_size(value: str, name: str) -> None:
    if len(value.encode("utf-8")) > _MAX_TEXT_BYTES:
        raise PrivateMemorySeedSqliteError(f"{name} too large")


def _validate_iso_timestamp(value: str) -> None:
    if len(value) > 64:
        raise PrivateMemorySeedSqliteError("timestamp too long")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PrivateMemorySeedSqliteError("invalid timestamp") from exc


def _optional_path(raw_path: object, base_path: Path) -> Path | None:
    if raw_path is None:
        return None
    if not isinstance(raw_path, str) or not raw_path:
        raise PrivateMemorySeedConfigError("invalid registry path")
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


def _safe_error_class(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,80}", value):
        return "PrivateMemorySeedImportError"
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
