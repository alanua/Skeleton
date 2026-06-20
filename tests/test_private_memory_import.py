from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

from core.private_memory import PRIVATE_MEMORY_CONFIG_SCHEMA
from core.private_memory_import import (
    PRIVATE_MEMORY_SEED_MANIFEST_SCHEMA,
    import_private_memory_seed,
)


def _write_seed_sqlite(
    path: Path,
    *,
    payload_class: str = "note",
    canonical_text: str = "synthetic canonical text",
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE seed_records (
                record_id TEXT PRIMARY KEY,
                payload_class TEXT NOT NULL,
                canonical_text TEXT NOT NULL,
                source_locator TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE seed_status_history (
                record_id TEXT NOT NULL,
                status TEXT NOT NULL,
                changed_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO seed_records (
                record_id, payload_class, canonical_text, source_locator, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "synthetic-record-001",
                payload_class,
                canonical_text,
                "synthetic-source-001",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO seed_status_history (record_id, status, changed_at)
            VALUES (?, ?, ?)
            """,
            ("synthetic-record-001", "created", "2026-01-01T00:00:00+00:00"),
        )


def _write_seed_zip(
    tmp_path: Path,
    *,
    payload_class: str = "note",
    bad_checksum: bool = False,
    unsafe_member: bool = False,
) -> Path:
    sqlite_path = tmp_path / "records.sqlite"
    _write_seed_sqlite(sqlite_path, payload_class=payload_class)
    sqlite_bytes = sqlite_path.read_bytes()
    checksum = hashlib.sha256(sqlite_bytes).hexdigest()
    if bad_checksum:
        checksum = "0" * 64
    manifest = {
        "schema": PRIVATE_MEMORY_SEED_MANIFEST_SCHEMA,
        "manifest_version": 1,
        "record_count": 1,
        "status_history_count": 1,
        "checksums": {"records.sqlite": checksum},
    }
    zip_path = tmp_path / "seed.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        archive.writestr("records.sqlite", sqlite_bytes)
        if unsafe_member:
            archive.writestr("../unsafe.txt", "nope")
    return zip_path


def _write_config(tmp_path: Path, zip_path: Path) -> Path:
    config_path = tmp_path / "private_memory_config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema": PRIVATE_MEMORY_CONFIG_SCHEMA,
                "database": {"path": str(tmp_path / "memory.sqlite")},
                "private_memory_seed": {"path": str(zip_path)},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _assert_public_safe(report: dict[str, object], tmp_path: Path) -> None:
    serialized = json.dumps(report, sort_keys=True)
    assert str(tmp_path) not in serialized
    assert "seed.zip" not in serialized
    assert "records.sqlite" not in serialized
    assert "memory.sqlite" not in serialized
    assert "synthetic canonical text" not in serialized
    assert "synthetic-source-001" not in serialized
    assert "path" not in serialized.lower()
    assert "payload" not in serialized.lower()
    assert "locator" not in serialized.lower()


def test_valid_synthetic_seed_imports_into_temporary_sqlite(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _write_seed_zip(tmp_path))

    report = import_private_memory_seed(write_enabled=True, config_path=config_path)

    assert report["status"] == "DONE"
    assert report["write_gate_open"] is True
    assert report["imported_record_count"] == 1
    assert report["status_history_count"] == 1
    assert report["audit_record_count"] == 3
    assert report["canonical_record_count"] == 1
    _assert_public_safe(report, tmp_path)

    with sqlite3.connect(tmp_path / "memory.sqlite") as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM private_memory_import_records").fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM private_memory_import_status_history"
            ).fetchone()[0]
            == 1
        )


def test_ungated_import_is_blocked_and_does_not_create_database(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _write_seed_zip(tmp_path))

    report = import_private_memory_seed(write_enabled=False, config_path=config_path)

    assert report["status"] == "BLOCKED"
    assert report["write_gate_open"] is False
    assert not (tmp_path / "memory.sqlite").exists()
    _assert_public_safe(report, tmp_path)


def test_bad_checksum_schema_archive_structure_are_blocked(tmp_path: Path) -> None:
    checksum_tmp = tmp_path / "checksum"
    checksum_tmp.mkdir()
    bad_checksum_config = _write_config(
        checksum_tmp,
        _write_seed_zip(checksum_tmp, bad_checksum=True),
    )

    bad_checksum_report = import_private_memory_seed(
        write_enabled=True,
        config_path=bad_checksum_config,
    )

    assert bad_checksum_report["status"] == "BLOCKED"
    assert bad_checksum_report["error_class"] == "PrivateMemorySeedChecksumError"

    unsafe_tmp = tmp_path / "unsafe"
    unsafe_tmp.mkdir()
    unsafe_config = _write_config(unsafe_tmp, _write_seed_zip(unsafe_tmp, unsafe_member=True))
    unsafe_report = import_private_memory_seed(write_enabled=True, config_path=unsafe_config)

    assert unsafe_report["status"] == "BLOCKED"
    assert unsafe_report["error_class"] == "PrivateMemorySeedArchiveError"

    payload_tmp = tmp_path / "payload"
    payload_tmp.mkdir()
    payload_config = _write_config(
        payload_tmp,
        _write_seed_zip(payload_tmp, payload_class="executable"),
    )
    payload_report = import_private_memory_seed(write_enabled=True, config_path=payload_config)

    assert payload_report["status"] == "BLOCKED"
    assert payload_report["error_class"] == "PrivateMemorySeedSqliteError"
    _assert_public_safe(bad_checksum_report, tmp_path)
    _assert_public_safe(unsafe_report, tmp_path)
    _assert_public_safe(payload_report, tmp_path)


def test_failed_import_rolls_back_and_preserves_original_database(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = _write_config(tmp_path, _write_seed_zip(tmp_path))
    db_path = tmp_path / "memory.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE original_marker (id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO original_marker (id) VALUES ('before')")

    import core.private_memory_import as importer

    def fail_records(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(importer, "_write_records", fail_records)

    report = import_private_memory_seed(write_enabled=True, config_path=config_path)

    assert report["status"] == "BLOCKED"
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT id FROM original_marker").fetchone()[0] == "before"
        table = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'private_memory_import_records'
            """
        ).fetchone()
        assert table is None
    _assert_public_safe(report, tmp_path)


def test_repeat_import_is_idempotent(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _write_seed_zip(tmp_path))

    first = import_private_memory_seed(write_enabled=True, config_path=config_path)
    second = import_private_memory_seed(write_enabled=True, config_path=config_path)

    assert first["status"] == "DONE"
    assert second["status"] == "DONE"
    assert second["idempotent"] is True
    assert second["imported_record_count"] == 0
    assert second["canonical_record_count"] == 1
    _assert_public_safe(second, tmp_path)
