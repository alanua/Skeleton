from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import core.private_memory as private_memory_module
from core.private_memory import CanonicalPrivateMemoryStore, PrivateMemoryWriteError
from core.private_memory_backup import (
    create_snapshot,
    restore_snapshot_to_isolated_target,
    snapshot_file_path,
)
from core.private_memory_history import bytes_hash, ensure_history_schema


def _store(tmp_path: Path) -> CanonicalPrivateMemoryStore:
    store = CanonicalPrivateMemoryStore(tmp_path / "synthetic-memory.sqlite")
    report = store.initialize()
    assert report["status"] == "DONE"
    return store


def _metadata() -> dict[str, str]:
    return {
        "actor_ref": "synthetic-actor",
        "reason_code": "synthetic-reason",
        "approval_ref": "approval-001",
        "transaction_ref": "txn-001",
    }


def _write_fact(
    store: CanonicalPrivateMemoryStore, value: object, *, fact_id: str = "fact-001"
) -> dict[str, object]:
    return store.put_fact(
        namespace="synthetic",
        fact_id=fact_id,
        value=value,
        **_metadata(),
    )


def _assert_public_safe(report: dict[str, object], tmp_path: Path) -> None:
    serialized = json.dumps(report, sort_keys=True)
    assert str(tmp_path) not in serialized
    assert "synthetic-memory.sqlite" not in serialized
    assert "private-value" not in serialized
    assert "raw-secret" not in serialized
    assert "path" not in serialized.lower()
    assert "SELECT" not in serialized


def _snapshot_proof(
    db_path: Path,
    snapshot_dir: Path,
    *,
    snapshot_id: str,
) -> dict[str, object]:
    manifest = create_snapshot(db_path, snapshot_dir, snapshot_id=snapshot_id)
    return {
        "manifest": manifest,
        "snapshot_path": snapshot_file_path(snapshot_dir, snapshot_id),
    }


def _table_count(db_path: Path, table_name: str) -> int:
    with sqlite3.connect(db_path) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def test_monotonic_revision_under_sequential_writes(tmp_path: Path) -> None:
    store = _store(tmp_path)

    first = _write_fact(store, {"label": "alpha"})
    second = _write_fact(store, {"label": "beta"})

    assert first["canonical_revision"] == 1
    assert second["canonical_revision"] == 2
    assert store.current_revision() == 2


def test_failed_transaction_does_not_increment_revision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_fact(store, {"label": "alpha"})

    with pytest.raises(ValueError):
        store.put_fact(
            namespace="synthetic",
            fact_id="bad/fact",
            value={"label": "bad"},
            **_metadata(),
        )

    assert store.current_revision() == 1


def test_update_preserves_previous_value_in_append_only_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_fact(store, {"label": "previous"})
    _write_fact(store, {"label": "current"})

    history = store.history(namespace="synthetic", fact_id="fact-001")

    assert len(history) == 2
    assert history[1]["previous_value"] == {"label": "previous"}
    assert history[1]["new_value"] == {"label": "current"}


def test_tombstone_hides_active_fact_but_preserves_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_fact(store, {"label": "private-value"})

    event = store.tombstone_fact(
        namespace="synthetic",
        fact_id="fact-001",
        event_type="delete",
        **_metadata(),
    )

    assert event["canonical_revision"] == 2
    assert store.get_active_fact(namespace="synthetic", fact_id="fact-001") is None
    history = store.history(namespace="synthetic", fact_id="fact-001")
    assert history[-1]["event_type"] == "delete"
    assert history[-1]["previous_value"] == {"label": "private-value"}


def test_physical_delete_of_canonical_facts_is_forbidden(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_fact(store, {"label": "alpha"})

    with sqlite3.connect(tmp_path / "synthetic-memory.sqlite") as connection:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("DELETE FROM private_memory_facts")


def test_snapshot_manifest_is_deterministic_and_path_safe(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_fact(store, {"label": "private-value"})

    manifest = create_snapshot(
        tmp_path / "synthetic-memory.sqlite",
        tmp_path / "snapshots",
        snapshot_id="snapshot-001",
        created_at="2026-01-01T00:00:00Z",
    )

    assert manifest["snapshot_id"] == "snapshot-001"
    assert manifest["canonical_revision"] == 1
    assert manifest["schema"] == "skeleton.private_memory.snapshot_manifest.v1"
    repeated_manifest = create_snapshot(
        tmp_path / "synthetic-memory.sqlite",
        tmp_path / "snapshots",
        snapshot_id="snapshot-001-repeat",
        created_at="2026-01-01T00:00:00Z",
    )
    comparable_manifest = {**manifest, "snapshot_id": "snapshot-001-repeat"}
    assert repeated_manifest == comparable_manifest
    assert set(manifest) == {
        "schema",
        "snapshot_id",
        "canonical_revision",
        "schema_version",
        "created_at",
        "aggregate_counts",
        "content_hash",
        "canonical_state_hash",
    }
    _assert_public_safe(manifest, tmp_path)


def test_restore_smoke_reproduces_revision_and_hashes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    event = _write_fact(store, {"label": "private-value"})
    manifest = create_snapshot(
        tmp_path / "synthetic-memory.sqlite",
        tmp_path / "snapshots",
        snapshot_id="snapshot-restore",
        created_at="2026-01-01T00:00:00Z",
    )

    report = restore_snapshot_to_isolated_target(
        snapshot_file_path(tmp_path / "snapshots", "snapshot-restore"),
        tmp_path / "isolated" / "restore.sqlite",
        manifest,
    )
    restored = CanonicalPrivateMemoryStore(tmp_path / "isolated" / "restore.sqlite")
    restored_history = restored.history(namespace="synthetic", fact_id="fact-001")

    assert report["status"] == "DONE"
    assert report["canonical_revision"] == 1
    assert report["activation_required"] is True
    assert report["activated"] is False
    assert report["next_operator_action"] == "request_separate_restore_activation_approval"
    assert restored_history[0]["new_hash"] == event["new_hash"]
    _assert_public_safe(report, tmp_path)


def test_restore_requires_separate_activation_gate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_fact(store, {"label": "private-value"})
    manifest = create_snapshot(
        tmp_path / "synthetic-memory.sqlite",
        tmp_path / "snapshots",
        snapshot_id="snapshot-gated",
    )

    report = restore_snapshot_to_isolated_target(
        snapshot_file_path(tmp_path / "snapshots", "snapshot-gated"),
        tmp_path / "isolated" / "restore.sqlite",
        manifest,
    )

    assert report["status"] == "DONE"
    assert report["activated"] is False
    assert (tmp_path / "isolated" / "restore.sqlite").exists()
    _assert_public_safe(report, tmp_path)


def test_corrupted_snapshot_fails_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_fact(store, {"label": "private-value"})
    manifest = create_snapshot(
        tmp_path / "synthetic-memory.sqlite",
        tmp_path / "snapshots",
        snapshot_id="snapshot-corrupt",
    )
    snapshot_path = snapshot_file_path(tmp_path / "snapshots", "snapshot-corrupt")
    snapshot_path.write_bytes(snapshot_path.read_bytes() + b"corruption")

    report = restore_snapshot_to_isolated_target(
        snapshot_path,
        tmp_path / "isolated" / "restore.sqlite",
        manifest,
    )

    assert report["status"] == "BLOCKED"
    assert report["error_class"] == "PrivateMemorySnapshotError"
    assert not (tmp_path / "isolated" / "restore.sqlite").exists()
    _assert_public_safe(report, tmp_path)


def test_existing_restore_target_is_not_overwritten_or_deleted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_fact(store, {"label": "private-value"})
    manifest = create_snapshot(
        tmp_path / "synthetic-memory.sqlite",
        tmp_path / "snapshots",
        snapshot_id="snapshot-existing-target",
    )
    target_path = tmp_path / "isolated" / "restore.sqlite"
    target_path.parent.mkdir()
    original_bytes = b"pre-existing-live-artifact"
    target_path.write_bytes(original_bytes)

    report = restore_snapshot_to_isolated_target(
        snapshot_file_path(tmp_path / "snapshots", "snapshot-existing-target"),
        target_path,
        manifest,
    )

    assert report["status"] == "BLOCKED"
    assert target_path.read_bytes() == original_bytes
    _assert_public_safe(report, tmp_path)


def test_restore_source_equals_target_fails_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_fact(store, {"label": "private-value"})
    manifest = create_snapshot(
        tmp_path / "synthetic-memory.sqlite",
        tmp_path / "snapshots",
        snapshot_id="snapshot-same-target",
    )
    snapshot_path = snapshot_file_path(tmp_path / "snapshots", "snapshot-same-target")
    before = snapshot_path.read_bytes()

    report = restore_snapshot_to_isolated_target(snapshot_path, snapshot_path, manifest)

    assert report["status"] == "BLOCKED"
    assert snapshot_path.read_bytes() == before
    _assert_public_safe(report, tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "skeleton.private_memory.sqlite.v999"),
        ("canonical_revision", 999),
        (
            "aggregate_counts",
            {"facts": 1, "events": 1, "history_entries": 999, "tombstones": 0},
        ),
    ],
)
def test_tampered_manifest_schema_revision_or_counts_fail_restore(
    tmp_path: Path, field: str, value: object
) -> None:
    store = _store(tmp_path)
    _write_fact(store, {"label": "private-value"})
    manifest = create_snapshot(
        tmp_path / "synthetic-memory.sqlite",
        tmp_path / "snapshots",
        snapshot_id=f"snapshot-tampered-{field}",
    )
    tampered_manifest = {**manifest, field: value}
    target_path = tmp_path / "isolated" / f"{field}.sqlite"

    report = restore_snapshot_to_isolated_target(
        snapshot_file_path(tmp_path / "snapshots", f"snapshot-tampered-{field}"),
        target_path,
        tampered_manifest,
    )

    assert report["status"] == "BLOCKED"
    assert not target_path.exists()
    _assert_public_safe(report, tmp_path)


def test_failed_restore_after_copy_leaves_no_target_artifact(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_fact(store, {"label": "private-value"})
    manifest = create_snapshot(
        tmp_path / "synthetic-memory.sqlite",
        tmp_path / "snapshots",
        snapshot_id="snapshot-invalid-copy",
    )
    snapshot_path = snapshot_file_path(tmp_path / "snapshots", "snapshot-invalid-copy")
    invalid_bytes = b"not-a-sqlite-database"
    snapshot_path.write_bytes(invalid_bytes)
    fabricated_manifest = {**manifest, "content_hash": bytes_hash(invalid_bytes)}
    target_path = tmp_path / "isolated" / "restore.sqlite"

    report = restore_snapshot_to_isolated_target(snapshot_path, target_path, fabricated_manifest)

    assert report["status"] == "BLOCKED"
    assert not target_path.exists()
    _assert_public_safe(report, tmp_path)


def test_duplicate_snapshot_id_fails_and_preserves_original_bytes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_fact(store, {"label": "alpha"})
    create_snapshot(
        tmp_path / "synthetic-memory.sqlite",
        tmp_path / "snapshots",
        snapshot_id="snapshot-duplicate",
    )
    snapshot_path = snapshot_file_path(tmp_path / "snapshots", "snapshot-duplicate")
    original_bytes = snapshot_path.read_bytes()
    _write_fact(store, {"label": "beta"})

    with pytest.raises(Exception):
        create_snapshot(
            tmp_path / "synthetic-memory.sqlite",
            tmp_path / "snapshots",
            snapshot_id="snapshot-duplicate",
        )

    assert snapshot_path.read_bytes() == original_bytes


def test_bulk_operation_requires_pre_operation_snapshot(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(PrivateMemoryWriteError):
        store.bulk_put_facts(
            [{"namespace": "synthetic", "fact_id": "fact-001", "value": {"label": "alpha"}}],
            **_metadata(),
        )

    assert store.current_revision() == 0


def test_bulk_operation_rejects_schema_only_or_fabricated_snapshot_proof(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_fact(store, {"label": "alpha"})
    schema_only = {"schema": "skeleton.private_memory.snapshot_manifest.v1"}
    fabricated = {
        "manifest": {
            "schema": "skeleton.private_memory.snapshot_manifest.v1",
            "snapshot_id": "snapshot-fabricated",
            "canonical_revision": 1,
            "schema_version": "skeleton.private_memory.sqlite.v1",
            "created_at": "2026-01-01T00:00:00Z",
            "aggregate_counts": {
                "facts": 1,
                "events": 1,
                "history_entries": 1,
                "tombstones": 0,
            },
            "content_hash": "0" * 64,
            "canonical_state_hash": "0" * 64,
        },
        "snapshot_path": tmp_path / "snapshots" / "missing.sqlite",
    }

    for proof in (schema_only, fabricated):
        with pytest.raises(PrivateMemoryWriteError):
            store.bulk_put_facts(
                [{"namespace": "synthetic", "fact_id": "fact-002", "value": {"label": "beta"}}],
                pre_operation_snapshot=proof,
                **_metadata(),
            )

    assert store.current_revision() == 1


def test_bulk_operation_rejects_stale_snapshot_proof(tmp_path: Path) -> None:
    store = _store(tmp_path)
    db_path = tmp_path / "synthetic-memory.sqlite"
    _write_fact(store, {"label": "alpha"})
    proof = _snapshot_proof(db_path, tmp_path / "snapshots", snapshot_id="snapshot-stale")
    _write_fact(store, {"label": "newer"}, fact_id="fact-002")

    with pytest.raises(PrivateMemoryWriteError):
        store.bulk_put_facts(
            [{"namespace": "synthetic", "fact_id": "fact-003", "value": {"label": "gamma"}}],
            pre_operation_snapshot=proof,
            **_metadata(),
        )

    assert store.current_revision() == 2
    assert store.get_active_fact(namespace="synthetic", fact_id="fact-003") is None


def test_bulk_operation_rejects_same_active_facts_with_different_provenance(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    db_path = tmp_path / "synthetic-memory.sqlite"
    _write_fact(store, {"label": "alpha"})
    proof = _snapshot_proof(db_path, tmp_path / "snapshots", snapshot_id="snapshot-provenance")
    snapshot_path = proof["snapshot_path"]
    assert isinstance(snapshot_path, Path)
    with sqlite3.connect(snapshot_path) as connection:
        connection.execute("DROP TRIGGER private_memory_no_event_update")
        connection.execute(
            "UPDATE private_memory_events SET approval_ref = ? WHERE canonical_revision = 1",
            ("approval-999",),
        )
    proof["manifest"] = {
        **proof["manifest"],
        "content_hash": bytes_hash(snapshot_path.read_bytes()),
    }

    with pytest.raises(PrivateMemoryWriteError):
        store.bulk_put_facts(
            [{"namespace": "synthetic", "fact_id": "fact-002", "value": {"label": "beta"}}],
            pre_operation_snapshot=proof,
            **_metadata(),
        )

    assert store.current_revision() == 1
    assert store.get_active_fact(namespace="synthetic", fact_id="fact-002") is None


def test_concurrent_stale_proof_cannot_mutate_between_verification_and_bulk_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    db_path = tmp_path / "synthetic-memory.sqlite"
    _write_fact(store, {"label": "alpha"})
    proof = _snapshot_proof(db_path, tmp_path / "snapshots", snapshot_id="snapshot-locked")
    original_verify = private_memory_module._verify_pre_operation_snapshot
    concurrent_blocked = False

    def verify_then_try_concurrent_write(
        connection: sqlite3.Connection, snapshot: object
    ) -> None:
        nonlocal concurrent_blocked
        original_verify(connection, snapshot)
        try:
            with sqlite3.connect(db_path, timeout=0.01) as other:
                other.execute("BEGIN IMMEDIATE")
                other.execute(
                    """
                    UPDATE private_memory_canonical_revision
                    SET current_revision = current_revision + 1
                    WHERE id = 1
                    """
                )
                other.commit()
        except sqlite3.OperationalError:
            concurrent_blocked = True

    monkeypatch.setattr(
        private_memory_module,
        "_verify_pre_operation_snapshot",
        verify_then_try_concurrent_write,
    )

    events = store.bulk_put_facts(
        [{"namespace": "synthetic", "fact_id": "fact-002", "value": {"label": "beta"}}],
        pre_operation_snapshot=proof,
        **_metadata(),
    )

    assert concurrent_blocked is True
    assert events[0]["canonical_revision"] == 2
    assert store.current_revision() == 2


def test_bulk_operation_rejects_snapshot_from_different_database(tmp_path: Path) -> None:
    left = _store(tmp_path / "left")
    right = _store(tmp_path / "right")
    _write_fact(left, {"label": "alpha"})
    _write_fact(right, {"label": "different"})
    proof = _snapshot_proof(
        tmp_path / "right" / "synthetic-memory.sqlite",
        tmp_path / "right" / "snapshots",
        snapshot_id="snapshot-other-db",
    )

    with pytest.raises(PrivateMemoryWriteError):
        left.bulk_put_facts(
            [{"namespace": "synthetic", "fact_id": "fact-002", "value": {"label": "beta"}}],
            pre_operation_snapshot=proof,
            **_metadata(),
        )

    assert left.current_revision() == 1
    assert left.get_active_fact(namespace="synthetic", fact_id="fact-002") is None


def test_partial_bulk_failure_leaves_revision_and_state_unchanged(tmp_path: Path) -> None:
    store = _store(tmp_path)
    db_path = tmp_path / "synthetic-memory.sqlite"
    _write_fact(store, {"label": "active"}, fact_id="active")
    _write_fact(store, {"label": "obsolete"}, fact_id="obsolete")
    store.tombstone_fact(namespace="synthetic", fact_id="obsolete", **_metadata())
    proof = _snapshot_proof(db_path, tmp_path / "snapshots", snapshot_id="snapshot-atomic")
    before_revision = store.current_revision()
    before_active = store.get_active_fact(namespace="synthetic", fact_id="active")
    before_obsolete = store.get_active_fact(namespace="synthetic", fact_id="obsolete")
    before_events = _table_count(db_path, "private_memory_events")
    before_history = _table_count(db_path, "private_memory_fact_history")
    before_tombstones = _table_count(db_path, "private_memory_tombstones")

    with pytest.raises(ValueError):
        store.bulk_put_facts(
            [
                {"namespace": "synthetic", "fact_id": "new-fact", "value": {"label": "new"}},
                {"namespace": "synthetic", "fact_id": "bad/fact", "value": {"label": "bad"}},
            ],
            pre_operation_snapshot=proof,
            **_metadata(),
        )

    assert store.current_revision() == before_revision
    assert store.get_active_fact(namespace="synthetic", fact_id="active") == before_active
    assert store.get_active_fact(namespace="synthetic", fact_id="obsolete") == before_obsolete
    assert store.get_active_fact(namespace="synthetic", fact_id="new-fact") is None
    assert _table_count(db_path, "private_memory_events") == before_events
    assert _table_count(db_path, "private_memory_fact_history") == before_history
    assert _table_count(db_path, "private_memory_tombstones") == before_tombstones


def test_traversal_shaped_snapshot_ids_fail(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_fact(store, {"label": "alpha"})

    with pytest.raises(Exception):
        create_snapshot(
            tmp_path / "synthetic-memory.sqlite",
            tmp_path / "snapshots",
            snapshot_id="../snapshot-escape",
        )
    with pytest.raises(Exception):
        snapshot_file_path(tmp_path / "snapshots", "..:snapshot")


def test_public_reports_leak_no_private_values(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_fact(store, {"label": "private-value", "raw": "raw-secret"})

    integrity = store.integrity_report()
    manifest = create_snapshot(
        tmp_path / "synthetic-memory.sqlite",
        tmp_path / "snapshots",
        snapshot_id="snapshot-public",
    )

    assert integrity["status"] == "DONE"
    _assert_public_safe(integrity, tmp_path)
    _assert_public_safe(manifest, tmp_path)


def test_canonical_operations_require_explicit_initialize(tmp_path: Path) -> None:
    db_path = tmp_path / "synthetic-memory.sqlite"
    store = CanonicalPrivateMemoryStore(db_path)

    report = store.integrity_report()
    with pytest.raises(Exception):
        store.put_fact(namespace="synthetic", fact_id="fact-001", value={"label": "alpha"}, **_metadata())

    assert report["status"] == "BLOCKED"
    assert report["next_operator_action"] == "inspect_private_memory_recovery"
    assert not db_path.exists()
    with pytest.raises(Exception):
        create_snapshot(db_path, tmp_path / "snapshots", snapshot_id="snapshot-uninitialized")


def test_existing_incomplete_schema_fails_closed_without_repair(tmp_path: Path) -> None:
    db_path = tmp_path / "synthetic-memory.sqlite"
    sqlite3.connect(db_path).close()
    store = CanonicalPrivateMemoryStore(db_path)

    report = store.integrity_report()
    with pytest.raises(Exception):
        store.current_revision()

    assert report["status"] == "BLOCKED"
    with sqlite3.connect(db_path) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    assert tables == []


@pytest.mark.parametrize(
    ("object_type", "object_name"),
    [
        ("table", "private_memory_meta"),
        ("index", "private_memory_events_fact_revision_idx"),
        ("trigger", "private_memory_no_event_update"),
    ],
)
def test_missing_required_schema_object_blocks_integrity(
    tmp_path: Path, object_type: str, object_name: str
) -> None:
    store = _store(tmp_path)
    with sqlite3.connect(tmp_path / "synthetic-memory.sqlite") as connection:
        connection.execute(f"DROP {object_type.upper()} {object_name}")

    report = store.integrity_report()

    assert report["status"] == "BLOCKED"
    assert report["error_class"] == "PrivateMemoryIntegrityFailure"


def test_no_op_required_trigger_blocks_integrity(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with sqlite3.connect(tmp_path / "synthetic-memory.sqlite") as connection:
        connection.execute("DROP TRIGGER private_memory_no_event_update")
        connection.execute(
            """
            CREATE TRIGGER private_memory_no_event_update
            BEFORE UPDATE ON private_memory_events
            BEGIN
                SELECT 1;
            END
            """
        )

    report = store.integrity_report()

    assert report["status"] == "BLOCKED"
    assert report["error_class"] == "PrivateMemoryIntegrityFailure"


def test_required_index_with_wrong_columns_blocks_integrity(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with sqlite3.connect(tmp_path / "synthetic-memory.sqlite") as connection:
        connection.execute("DROP INDEX private_memory_events_fact_revision_idx")
        connection.execute(
            """
            CREATE INDEX private_memory_events_fact_revision_idx
            ON private_memory_events (namespace, canonical_revision)
            """
        )

    report = store.integrity_report()

    assert report["status"] == "BLOCKED"
    assert report["error_class"] == "PrivateMemoryIntegrityFailure"


def test_missing_current_fact_row_blocks_integrity_after_delete_trigger_restored(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _write_fact(store, {"label": "private-value"})
    store.tombstone_fact(namespace="synthetic", fact_id="fact-001", **_metadata())
    with sqlite3.connect(tmp_path / "synthetic-memory.sqlite") as connection:
        connection.execute("DROP TRIGGER private_memory_no_fact_delete")
        connection.execute(
            "DELETE FROM private_memory_facts WHERE namespace = ? AND fact_id = ?",
            ("synthetic", "fact-001"),
        )
        ensure_history_schema(connection)

    report = store.integrity_report()

    assert report["status"] == "BLOCKED"
    assert report["error_class"] == "PrivateMemoryIntegrityFailure"


def test_fact_created_at_must_match_first_event_timestamp(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_fact(store, {"label": "alpha"})
    _write_fact(store, {"label": "beta"})
    with sqlite3.connect(tmp_path / "synthetic-memory.sqlite") as connection:
        connection.execute(
            "UPDATE private_memory_facts SET created_at = ? WHERE namespace = ? AND fact_id = ?",
            ("2026-01-01T00:00:00Z", "synthetic", "fact-001"),
        )

    report = store.integrity_report()

    assert report["status"] == "BLOCKED"


def test_tombstone_reason_must_match_destructive_event_reason(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_fact(store, {"label": "alpha"})
    store.tombstone_fact(namespace="synthetic", fact_id="fact-001", **_metadata())
    with sqlite3.connect(tmp_path / "synthetic-memory.sqlite") as connection:
        connection.execute(
            "UPDATE private_memory_facts SET tombstone_reason = ? WHERE namespace = ? AND fact_id = ?",
            ("different-reason", "synthetic", "fact-001"),
        )

    report = store.integrity_report()

    assert report["status"] == "BLOCKED"


def test_canonical_revision_updated_at_must_match_latest_event_timestamp(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_fact(store, {"label": "alpha"})
    _write_fact(store, {"label": "beta"})
    with sqlite3.connect(tmp_path / "synthetic-memory.sqlite") as connection:
        connection.execute(
            "UPDATE private_memory_canonical_revision SET updated_at = ? WHERE id = 1",
            ("2026-01-01T00:00:00Z",),
        )

    report = store.integrity_report()

    assert report["status"] == "BLOCKED"


@pytest.mark.parametrize(
    ("table_name", "trigger_name", "sql", "params"),
    [
        (
            "private_memory_facts",
            None,
            "UPDATE private_memory_facts SET value_json = ? WHERE fact_id = ?",
            ('{"label":"tampered"}', "fact-001"),
        ),
        (
            "private_memory_events",
            "private_memory_no_event_update",
            "UPDATE private_memory_events SET actor_ref = ? WHERE canonical_revision = 2",
            ("actor-999",),
        ),
        (
            "private_memory_fact_history",
            "private_memory_no_history_update",
            "UPDATE private_memory_fact_history SET new_value_json = ? WHERE canonical_revision = 1",
            ('{"label":"tampered"}',),
        ),
        (
            "private_memory_tombstones",
            "private_memory_no_tombstone_update",
            "UPDATE private_memory_tombstones SET reason_code = ? WHERE canonical_revision = 2",
            ("reason-999",),
        ),
    ],
)
def test_modified_canonical_content_fails_integrity(
    tmp_path: Path,
    table_name: str,
    trigger_name: str | None,
    sql: str,
    params: tuple[object, ...],
) -> None:
    store = _store(tmp_path)
    _write_fact(store, {"label": "private-value"})
    if table_name in {"private_memory_events", "private_memory_tombstones"}:
        store.tombstone_fact(namespace="synthetic", fact_id="fact-001", **_metadata())
    with sqlite3.connect(tmp_path / "synthetic-memory.sqlite") as connection:
        if trigger_name is not None:
            connection.execute(f"DROP TRIGGER {trigger_name}")
        connection.execute(sql, params)

    report = store.integrity_report()

    assert report["status"] == "BLOCKED"
    assert report["error_class"] == "PrivateMemoryIntegrityFailure"
