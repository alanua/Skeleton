from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest import mock

from scripts import runner_poll_github_tasks as runner


def _env(db_path: Path, ledger_path: Path) -> dict[str, str]:
    return {
        runner.RUNNER_MEMORY_DB_ENV: str(db_path),
        runner.RUNNER_MEMORY_LEDGER_ENV: str(ledger_path),
    }


def _ledger_rows(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sqlite_counts(db_path: Path) -> dict[str, int]:
    connection = sqlite3.connect(db_path)
    return {
        "memory_events": connection.execute(
            "SELECT COUNT(*) FROM memory_events"
        ).fetchone()[0],
        "project_state": connection.execute(
            "SELECT COUNT(*) FROM project_state"
        ).fetchone()[0],
        "decision_records": connection.execute(
            "SELECT COUNT(*) FROM decision_records"
        ).fetchone()[0],
    }


def test_recent_skeleton_memory_backfill_writes_expected_counts(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "skeleton.db"
    ledger_path = tmp_path / "events.jsonl"

    with mock.patch.dict(os.environ, _env(db_path, ledger_path), clear=True):
        report = runner.backfill_skeleton_memory_recent()

    assert report.startswith("DONE:")
    assert "memory_events_written=7" in report
    assert "project_state_written=1" in report
    assert "ledger_events_written=8" in report
    assert "decision_records_written=0" in report
    assert "decision_records_skipped=1" in report

    counts = _sqlite_counts(db_path)
    assert counts == {
        "memory_events": 7,
        "project_state": 1,
        "decision_records": 0,
    }
    assert len(_ledger_rows(ledger_path)) == 8


def test_recent_skeleton_memory_backfill_blocks_when_config_absent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "skeleton.db"
    ledger_path = tmp_path / "events.jsonl"

    with mock.patch.dict(os.environ, {}, clear=True):
        report = runner.backfill_skeleton_memory_recent()

    assert report.startswith("BLOCKED:")
    assert "reason=runner_memory_config_missing" in report
    assert not db_path.exists()
    assert not ledger_path.exists()


def test_recent_skeleton_memory_backfill_never_writes_private_material(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "skeleton.db"
    ledger_path = tmp_path / "events.jsonl"
    forbidden = (
        "/home/agent/",
        "drive.google.com",
        "docs.google.com",
        ".env",
        "OPENAI_API_KEY",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "PASSWORD",
        "SECRET",
        "TOKEN=",
        "sk-",
        "github_pat_",
    )

    with mock.patch.dict(os.environ, _env(db_path, ledger_path), clear=True):
        report = runner.backfill_skeleton_memory_recent()

    assert report.startswith("DONE:")
    connection = sqlite3.connect(db_path)
    sqlite_text = "\n".join(
        row[0]
        for row in connection.execute(
            """
            SELECT metadata_json FROM memory_events
            UNION ALL
            SELECT state_json FROM project_state
            UNION ALL
            SELECT metadata_json FROM decision_records
            """
        )
    )
    ledger_text = ledger_path.read_text(encoding="utf-8")
    combined = f"{sqlite_text}\n{ledger_text}\n{report}"
    for marker in forbidden:
        assert marker not in combined


def test_recent_skeleton_memory_backfill_is_duplicate_safe(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "skeleton.db"
    ledger_path = tmp_path / "events.jsonl"

    with mock.patch.dict(os.environ, _env(db_path, ledger_path), clear=True):
        first_report = runner.backfill_skeleton_memory_recent()
        second_report = runner.backfill_skeleton_memory_recent()

    assert first_report.startswith("DONE:")
    assert second_report.startswith("DONE:")
    assert "memory_events_written=0" in second_report
    assert "memory_events_existing=7" in second_report
    assert "project_state_written=0" in second_report
    assert "project_state_existing=1" in second_report
    assert "ledger_events_written=0" in second_report
    assert _sqlite_counts(db_path) == {
        "memory_events": 7,
        "project_state": 1,
        "decision_records": 0,
    }
    assert len(_ledger_rows(ledger_path)) == 8


def test_recent_skeleton_memory_backfill_dispatch_is_allowlisted(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "skeleton.db"
    ledger_path = tmp_path / "events.jsonl"

    with mock.patch.dict(os.environ, _env(db_path, ledger_path), clear=True):
        report = runner.dispatch_runtime_maintenance_task(
            runner.BACKFILL_SKELETON_MEMORY_RECENT, str(tmp_path)
        )

    assert report.startswith("DONE:")
    assert "maintenance_task_id=backfill_skeleton_memory_recent" in report
