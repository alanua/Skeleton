from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.home_edge.audit_persist import persist_home_edge_runtime_audit


def runtime_audit(audit_id: str = "audit-2074-a") -> dict[str, object]:
    return {
        "schema": "skeleton.home_edge.runtime_audit.v1",
        "audit_id": audit_id,
        "kind": "RUNTIME_AUDIT",
        "device_id": "home_edge_01",
        "execution_node": "home-edge-01",
        "status": "verified",
        "evidence_ref": "skeleton-audit-receipt",
    }


def counts(root: Path, audit_id: str) -> dict[str, int]:
    with sqlite3.connect(root / "canonical.sqlite") as connection:
        semantic = connection.execute(
            "SELECT COUNT(*) FROM private_memory_events WHERE transaction_ref = ?",
            (audit_id,),
        ).fetchone()[0]
        facts = connection.execute(
            """
            SELECT COUNT(*) FROM private_memory_facts
            WHERE namespace = 'skeleton.home_edge.runtime_audits' AND fact_id = ?
            """,
            (audit_id,),
        ).fetchone()[0]
    with sqlite3.connect(root / "memory_gateway_mutations.sqlite") as connection:
        metadata = connection.execute(
            "SELECT COUNT(*) FROM home_edge_runtime_audit_metadata WHERE audit_id = ?",
            (audit_id,),
        ).fetchone()[0]
        state = connection.execute(
            "SELECT COUNT(*) FROM home_edge_runtime_audit_state WHERE device_id = 'home_edge_01'",
        ).fetchone()[0]
        history = connection.execute(
            "SELECT COUNT(*) FROM home_edge_runtime_audit_state_history WHERE audit_id = ?",
            (audit_id,),
        ).fetchone()[0]
    return {
        "semantic": int(semantic),
        "facts": int(facts),
        "metadata": int(metadata),
        "state": int(state),
        "history": int(history),
    }


def test_first_write_persists_semantic_record_and_sqlite_metadata(tmp_path: Path) -> None:
    receipt = persist_home_edge_runtime_audit(runtime_audit(), private_root=tmp_path)

    assert receipt["status"] == "DONE"
    assert receipt["idempotency_classification"] == "NEW_MUTATION"
    assert receipt["verification"] == {
        "canonical_record_count": 1,
        "sqlite_metadata_count": 1,
        "sqlite_state_count": 1,
        "sqlite_history_count": 1,
    }
    assert counts(tmp_path, "audit-2074-a") == {
        "semantic": 1,
        "facts": 1,
        "metadata": 1,
        "state": 1,
        "history": 1,
    }


def test_exact_replay_succeeds_without_duplicates(tmp_path: Path) -> None:
    payload = runtime_audit()
    first = persist_home_edge_runtime_audit(payload, private_root=tmp_path)
    replay = persist_home_edge_runtime_audit(payload, private_root=tmp_path)

    assert first["canonical_revision"] == replay["canonical_revision"]
    assert replay["idempotency_classification"] == "DUPLICATE_IDENTICAL"
    assert counts(tmp_path, "audit-2074-a") == {
        "semantic": 1,
        "facts": 1,
        "metadata": 1,
        "state": 1,
        "history": 1,
    }


def test_conflicting_duplicate_audit_id_fails_safely(tmp_path: Path) -> None:
    payload = runtime_audit()
    persist_home_edge_runtime_audit(payload, private_root=tmp_path)
    conflicting = dict(payload)
    conflicting["status"] = "changed"

    with pytest.raises(Exception) as excinfo:
        persist_home_edge_runtime_audit(conflicting, private_root=tmp_path)

    assert "different" in str(excinfo.value)
    assert counts(tmp_path, "audit-2074-a") == {
        "semantic": 1,
        "facts": 1,
        "metadata": 1,
        "state": 1,
        "history": 1,
    }


def test_partial_sqlite_failure_retries_and_rolls_forward_without_duplicate_semantic_record(
    tmp_path: Path,
) -> None:
    payload = runtime_audit("audit-2074-partial")

    with pytest.raises(Exception, match="injected home edge audit sqlite failure"):
        persist_home_edge_runtime_audit(
            payload,
            private_root=tmp_path,
            inject_sqlite_failure=True,
        )

    assert counts(tmp_path, "audit-2074-partial") == {
        "semantic": 1,
        "facts": 1,
        "metadata": 1,
        "state": 0,
        "history": 0,
    }

    receipt = persist_home_edge_runtime_audit(payload, private_root=tmp_path)

    assert receipt["idempotency_classification"] == "DUPLICATE_IDENTICAL"
    assert counts(tmp_path, "audit-2074-partial") == {
        "semantic": 1,
        "facts": 1,
        "metadata": 1,
        "state": 1,
        "history": 1,
    }


def test_no_plain_runtime_payload_is_written_to_gateway_sqlite(tmp_path: Path) -> None:
    payload = runtime_audit("audit-2074-boundary")
    payload["private_note"] = "do-not-copy-runtime-body"
    persist_home_edge_runtime_audit(payload, private_root=tmp_path)

    blob = (tmp_path / "memory_gateway_mutations.sqlite").read_bytes()

    assert b"do-not-copy-runtime-body" not in blob
    assert b"private_note" not in blob
