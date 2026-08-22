from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from core.private_memory_backup import (
    SNAPSHOT_MANIFEST,
    load_snapshot_manifest,
    manifest_file_path,
    snapshot_file_path,
)
from core.private_memory_stack import PrivateMemoryStack


def test_backup_manifest_verifies_schema_revision_hash_and_counts(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    stack.put(namespace="skeleton.notes", fact_id="note1", value={"summary": "synthetic backup"})

    backup = stack.backup(snapshot_id="snapshot-ok")
    manifest = load_snapshot_manifest(tmp_path / "backups", "snapshot-ok")
    verification = stack.verify_backup(snapshot_id="snapshot-ok")

    assert backup["status"] == "DONE"
    assert backup["hash_class"] == "sha256"
    assert backup["aggregate_counts"]["facts"] == 1
    assert manifest["schema"] == SNAPSHOT_MANIFEST
    assert manifest["schema_version"] == "skeleton.private_memory.sqlite.v1"
    assert manifest["canonical_revision"] == backup["canonical_revision"]
    assert manifest["hash_class"] == "sha256"
    assert isinstance(manifest["content_hash"], str)
    assert isinstance(manifest["sqlite_schema_hash"], str)
    assert verification["status"] == "DONE"
    assert verification["revision_classification"] == "MATCH"
    assert verification["aggregate_counts"]["facts"] == 1
    assert "content_hash" not in verification


def test_corrupted_snapshot_is_rejected_without_private_records(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    stack.put(namespace="skeleton.notes", fact_id="note1", value={"summary": "synthetic corrupt"})
    stack.backup(snapshot_id="snapshot-corrupt")

    snapshot = snapshot_file_path(tmp_path / "backups", "snapshot-corrupt")
    with snapshot.open("r+b") as handle:
        handle.seek(0)
        handle.write(b"not sqlite")

    verification = stack.verify_backup(snapshot_id="snapshot-corrupt")

    assert verification["status"] == "BLOCKED"
    assert verification["error_class"] == "PrivateMemorySnapshotError"
    serialized = json.dumps(verification)
    assert "synthetic corrupt" not in serialized
    assert str(tmp_path) not in serialized


def test_foreign_schema_snapshot_is_rejected(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    stack.backup(snapshot_id="snapshot-foreign")
    snapshot = snapshot_file_path(tmp_path / "backups", "snapshot-foreign")

    snapshot.unlink()
    with sqlite3.connect(snapshot) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY, value TEXT)")
        connection.commit()
    manifest_path = manifest_file_path(tmp_path / "backups", "snapshot-foreign")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["file_size_bytes"] = snapshot.stat().st_size
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    verification = stack.verify_backup(snapshot_id="snapshot-foreign")

    assert verification["status"] == "BLOCKED"
    assert verification["error_class"] == "PrivateMemorySnapshotError"


def test_stale_backup_is_classified_and_not_restored_over_newer_canon(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    stack.put(namespace="skeleton.notes", fact_id="old", value={"summary": "old synthetic"})
    stack.backup(snapshot_id="snapshot-stale")
    before_revision = stack.status()["canonical_sqlite"]["canonical_revision"]

    stack.put(namespace="skeleton.notes", fact_id="new", value={"summary": "new synthetic"})
    current = stack.status()["canonical_sqlite"]["canonical_revision"]
    verification = stack.verify_backup(snapshot_id="snapshot-stale")
    dry_run = stack.dry_run_restore(snapshot_id="snapshot-stale")

    assert current > before_revision
    assert verification["status"] == "STALE"
    assert verification["revision_classification"] == "STALE"
    assert verification["current_canonical_revision"] == current
    assert dry_run["status"] == "BLOCKED"
    assert dry_run["revision_classification"] == "STALE"
    assert stack.get(namespace="skeleton.notes", fact_id="new")["value"]["summary"] == "new synthetic"


def test_dry_run_restore_verifies_readback_without_mutating_canonical_db(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    stack.put(namespace="skeleton.notes", fact_id="note1", value={"summary": "dry run synthetic"})
    stack.backup(snapshot_id="snapshot-dry-run")
    before_bytes = (tmp_path / "canonical.sqlite").read_bytes()
    before_status = stack.status()

    dry_run = stack.dry_run_restore(snapshot_id="snapshot-dry-run")

    assert dry_run["status"] == "DONE"
    assert dry_run["activation_required"] is True
    assert dry_run["activated"] is False
    assert dry_run["next_operator_action"] == "request_separate_restore_activation_approval"
    assert dry_run["derived_projections_rebuild_required"] == ["mempalace", "graphify", "cognee"]
    assert (tmp_path / "canonical.sqlite").read_bytes() == before_bytes
    assert stack.status() == before_status
