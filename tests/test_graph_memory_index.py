from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

from core.graph_memory_index import build_graph_memory_index
from core.private_memory import PRIVATE_MEMORY_CONFIG_SCHEMA
from core.private_memory_import import PRIVATE_MEMORY_SEED_MANIFEST_SCHEMA, import_private_memory_seed


def _write_seed_package(tmp_path: Path) -> Path:
    sqlite_path = tmp_path / "records.sqlite"
    with sqlite3.connect(sqlite_path) as connection:
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
            INSERT INTO seed_records VALUES (
                'synthetic-record-001', 'note', 'synthetic text',
                'synthetic-source-001', '2026-01-01T00:00:00+00:00'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO seed_status_history VALUES (
                'synthetic-record-001', 'created', '2026-01-01T00:00:00+00:00'
            )
            """
        )
    sqlite_bytes = sqlite_path.read_bytes()
    manifest = {
        "schema": PRIVATE_MEMORY_SEED_MANIFEST_SCHEMA,
        "manifest_version": 1,
        "record_count": 1,
        "status_history_count": 1,
        "checksums": {"records.sqlite": hashlib.sha256(sqlite_bytes).hexdigest()},
    }
    zip_path = tmp_path / "seed.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        archive.writestr("records.sqlite", sqlite_bytes)
    return zip_path


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "private_memory_config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema": PRIVATE_MEMORY_CONFIG_SCHEMA,
                "database": {"path": str(tmp_path / "memory.sqlite")},
                "private_memory_seed": {
                    "path": str(_write_seed_package(tmp_path)),
                    "graph_output_dir": str(tmp_path / "graph"),
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return config_path


def test_derived_graph_is_rebuilt_with_provenance(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    import_report = import_private_memory_seed(write_enabled=True, config_path=config_path)

    report = build_graph_memory_index(config_path=config_path)

    assert import_report["status"] == "DONE"
    assert report["status"] == "DONE"
    assert report["source_record_count"] == 1
    assert report["node_count"] == 3
    assert report["edge_count"] == 2
    assert report["provenance_record_count"] == 1
    assert report["canonical_write_attempted"] is False
    graph_json = json.loads((tmp_path / "graph" / "private_memory_graph_index.json").read_text())
    graphml = (tmp_path / "graph" / "private_memory_graph_index.graphml").read_text()
    assert len(graph_json["provenance"]) == 1
    assert "private_memory_import_records" in graph_json["provenance"][0]["derived_from"]
    assert "<graphml" in graphml
    assert "<edge" in graphml


def test_graph_report_is_aggregate_only(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    import_private_memory_seed(write_enabled=True, config_path=config_path)

    report = build_graph_memory_index(config_path=config_path)

    serialized = json.dumps(report, sort_keys=True)
    assert str(tmp_path) not in serialized
    assert "memory.sqlite" not in serialized
    assert "synthetic text" not in serialized
    assert "synthetic-source-001" not in serialized
    assert "path" not in serialized.lower()
    assert "payload" not in serialized.lower()
