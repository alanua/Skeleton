from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from core.skeleton_memory import SkeletonMemory


def table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows}


def test_schema_initializes_expected_tables() -> None:
    memory = SkeletonMemory()
    memory.init_schema()

    assert {
        "memory_events",
        "project_state",
        "executor_runs",
        "decision_records",
        "canon_candidates",
        "private_reference_stubs",
        "canonical_memory_records",
        "canonical_import_snapshots",
        "canonical_import_receipts",
    }.issubset(table_names(memory.connection))


def test_executor_run_can_be_logged_and_read_through_tables(tmp_path: Path) -> None:
    memory = SkeletonMemory(tmp_path / "skeleton-memory.sqlite")
    memory.init_schema()

    run_id = memory.log_executor_run(
        {
            "project_id": "skeleton",
            "executor": "codex",
            "status": "completed",
            "summary": "stage 1 memory tests",
        }
    )

    row = memory.connection.execute("SELECT * FROM executor_runs WHERE id = ?", (run_id,)).fetchone()
    assert row["project_id"] == "skeleton"
    assert row["executor"] == "codex"
    assert row["status"] == "completed"
    assert json.loads(row["metadata_json"])["summary"] == "stage 1 memory tests"

    event = memory.connection.execute(
        "SELECT * FROM memory_events WHERE event_type = 'executor_run_logged'"
    ).fetchone()
    assert event["project_id"] == "skeleton"
    assert json.loads(event["metadata_json"])["executor_run_id"] == run_id


def test_project_state_can_be_updated_and_read() -> None:
    memory = SkeletonMemory()
    memory.init_schema()

    memory.update_project_state("skeleton", {"phase": "stage-1", "queue_depth": 2})
    memory.update_project_state("skeleton", {"phase": "stage-1-complete", "queue_depth": 0})

    assert memory.get_project_state("skeleton") == {
        "phase": "stage-1-complete",
        "queue_depth": 0,
    }
    assert memory.get_project_state("missing") is None


def test_canon_candidate_requires_explicit_operator_approval_method() -> None:
    memory = SkeletonMemory()
    memory.init_schema()

    candidate_id = memory.submit_canon_candidate(
        {
            "project_id": "skeleton",
            "summary": "SQLite stores operational state.",
            "public_safe": True,
        }
    )

    pending = memory.connection.execute(
        "SELECT status, operator, approved_at FROM canon_candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    assert pending["status"] == "pending"
    assert pending["operator"] is None
    assert pending["approved_at"] is None

    memory.approve_canon_candidate(candidate_id, operator="operator-1")

    approved = memory.connection.execute(
        "SELECT status, operator, approved_at FROM canon_candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    assert approved["status"] == "approved"
    assert approved["operator"] == "operator-1"
    assert approved["approved_at"].endswith("Z")


def test_skeleton_memory_rejects_secret_looking_payloads() -> None:
    memory = SkeletonMemory()
    memory.init_schema()

    with pytest.raises(ValueError, match="secret field"):
        memory.log_operator_event({"event_type": "note", "api_token": "abc"})
