from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest import mock

from scripts import runner_poll_github_tasks as runner


HEAD_SHA = "a" * 40
PR_URL = "https://github.com/alanua/Skeleton/pull/123"


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


def _done_report(extra_pytest: str = "1 passed") -> str:
    return (
        "DONE: Codex completed successfully and produced file changes.\n\n"
        "Changed files:\n"
        "- scripts/runner_poll_github_tasks.py\n"
        "- tests/test_runner_memory_integration.py\n\n"
        f"Pytest output:\n```\n{extra_pytest}\n```\n\n"
        f"Commit: {HEAD_SHA}\n"
        f"Draft PR: {PR_URL}"
    )


def test_memory_disabled_path_does_not_write_and_does_not_fail(tmp_path: Path) -> None:
    db_path = tmp_path / "skeleton.db"
    ledger_path = tmp_path / "events.jsonl"

    with mock.patch.dict(os.environ, {}, clear=True):
        warning = runner.record_runner_executor_result(
            464, "skeleton", "DONE", "DONE", "codex", _done_report()
        )

    assert warning is None
    assert not db_path.exists()
    assert not ledger_path.exists()


def test_memory_enabled_path_writes_executor_result_to_sqlite_and_jsonl(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "skeleton.db"
    ledger_path = tmp_path / "events.jsonl"

    with mock.patch.dict(os.environ, _env(db_path, ledger_path), clear=True):
        warning = runner.record_runner_executor_result(
            464, "skeleton", "DONE", "DONE", "codex", _done_report("2 passed")
        )

    assert warning is None
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    row = connection.execute("SELECT * FROM executor_runs").fetchone()
    metadata = json.loads(row["metadata_json"])
    assert row["project_id"] == "skeleton"
    assert row["executor"] == "codex"
    assert row["status"] == "DONE"
    assert metadata["issue_number"] == 464
    assert metadata["changed_files"] == [
        "scripts/runner_poll_github_tasks.py",
        "tests/test_runner_memory_integration.py",
    ]
    assert metadata["test_summary"] == "2 passed"
    assert metadata["pr_url"] == PR_URL

    rows = _ledger_rows(ledger_path)
    assert rows[-1]["event_type"] == "runner_task_executor_result"
    assert rows[-1]["status"] == "DONE"


def test_blocked_task_writes_sanitized_event_without_raw_transcript(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "skeleton.db"
    ledger_path = tmp_path / "events.jsonl"
    report = runner.blocked_codex_output_report(
        "BLOCKED: see private file /home/agent/private/customer.txt\n"
        "OPENAI_API_KEY=sk-secret-must-not-appear",
        "BLOCKED",
        "/home/agent/agent-dev/worktrees/skeleton/issue-464",
    )

    with mock.patch.dict(os.environ, _env(db_path, ledger_path), clear=True):
        warning = runner.record_runner_executor_result(
            464, "skeleton", "BLOCKED", "BLOCKED", "codex", report
        )

    assert warning is None
    ledger_text = ledger_path.read_text(encoding="utf-8")
    assert "private/customer" not in ledger_text
    assert "OPENAI_API_KEY" not in ledger_text
    assert "sk-secret" not in ledger_text
    assert "issue-464" not in ledger_text
    assert _ledger_rows(ledger_path)[-1]["status"] == "BLOCKED"


def test_secret_looking_output_is_redacted_before_ledger_append(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "skeleton.db"
    ledger_path = tmp_path / "events.jsonl"

    with mock.patch.dict(os.environ, _env(db_path, ledger_path), clear=True):
        warning = runner.record_runner_executor_result(
            464,
            "skeleton",
            "DONE",
            "DONE",
            "codex",
            _done_report("1 passed API_KEY=sk-secret-must-not-appear"),
        )

    assert warning is None
    ledger_text = ledger_path.read_text(encoding="utf-8")
    assert "API_KEY" not in ledger_text
    assert "sk-secret" not in ledger_text
    assert "redacted unsafe test summary" in ledger_text


def test_drive_url_private_path_and_env_content_are_not_written(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "skeleton.db"
    ledger_path = tmp_path / "events.jsonl"
    report = (
        "DONE: Codex completed successfully and produced file changes.\n\n"
        "Changed files:\n"
        "- /home/agent/private/customer.txt\n"
        "- .env\n"
        "- scripts/runner_poll_github_tasks.py\n\n"
        "Pytest output:\n```\n1 passed\nSKELETON_TG_BOT=secret\n```\n\n"
        "Codex output:\n```\n"
        "https://drive.google.com/file/d/private/view\n"
        "/home/agent/private/customer.txt\n"
        "PASSWORD=not-for-memory\n"
        "```\n\n"
        "Draft PR: https://drive.google.com/file/d/private/view"
    )

    with mock.patch.dict(os.environ, _env(db_path, ledger_path), clear=True):
        warning = runner.record_runner_executor_result(
            464, "skeleton", "DONE", "DONE", "codex", report
        )

    assert warning is None
    ledger_text = ledger_path.read_text(encoding="utf-8")
    assert "drive.google.com" not in ledger_text
    assert "/home/agent/private" not in ledger_text
    assert "SKELETON_TG_BOT" not in ledger_text
    assert "PASSWORD" not in ledger_text
    assert ".env" not in ledger_text
    assert "scripts/runner_poll_github_tasks.py" in ledger_text


def test_memory_write_failure_returns_warning_without_changing_task_status(
    tmp_path: Path,
) -> None:
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("file blocks sqlite parent", encoding="utf-8")

    with mock.patch.dict(
        os.environ,
        _env(blocked_parent / "skeleton.db", tmp_path / "events.jsonl"),
        clear=True,
    ):
        warning = runner.record_runner_executor_result(
            464, "skeleton", "DONE", "DONE", "codex", _done_report()
        )

    assert warning == runner.RUNNER_MEMORY_WARNING
    assert runner.append_memory_warning("DONE: kept", warning).startswith("DONE: kept")
