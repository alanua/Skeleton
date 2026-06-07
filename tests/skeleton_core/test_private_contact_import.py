from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.skeleton_core.private_contact_import import (
    AUTH_INVALID,
    AUTH_MISSING,
    AUTH_READY,
    IMPORT_READY,
    SHEET_ACCESS_MISSING,
    ContactImportError,
    ContactImportResult,
    SheetAccessError,
    _load_task,
    post_safe_github_status_comment,
    process_private_contact_import,
)


SHEET_A = "synthetic_sheet_id_A_0000000000"
SHEET_B = "synthetic_sheet_id_B_0000000000"


class FakeSheetsClient:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], list[list[object]]] = {
            (SHEET_A, "staging"): [["name", "phone"], ["Alpha", "private"], ["Beta", "private"]],
            (SHEET_B, "RAW_IMPORT"): [["name", "phone"], ["Alpha", "private"]],
        }
        self.append_calls: list[tuple[str, str, list[list[object]]]] = []

    def read_values(self, sheet_id: str, tab_name: str) -> list[list[object]]:
        return self.values[(sheet_id, tab_name)]

    def append_values(self, sheet_id: str, tab_name: str, values: list[list[object]]) -> None:
        self.append_calls.append((sheet_id, tab_name, values))
        self.values[(sheet_id, tab_name)].extend(values)


class BlockedSheetsClient(FakeSheetsClient):
    def read_values(self, sheet_id: str, tab_name: str) -> list[list[object]]:
        raise SheetAccessError("Configured credentials cannot access one or more configured sheets.")


def _write_task(task_dir: Path, **updates: object) -> Path:
    task = {
        "staging_sheet_id": SHEET_A,
        "staging_tab": "staging",
        "target_sheet_id": SHEET_B,
        "target_tab": "RAW_IMPORT",
        "mode": "append",
        "dedupe_policy": "skip_exact_rows",
    }
    task.update(updates)
    task_path = task_dir / "contact-import.json"
    task_path.write_text(json.dumps(task), encoding="utf-8")
    return task_path


def test_check_only_reports_auth_missing_without_google_credentials(tmp_path: Path) -> None:
    result = process_private_contact_import(
        task_dir=tmp_path / "in",
        result_dir=tmp_path / "out",
        check_only=True,
        client_factory=lambda: (_ for _ in ()).throw(FileNotFoundError()),
    )

    assert result.status == AUTH_MISSING
    report = json.loads((tmp_path / "out" / "auth_check_result.json").read_text(encoding="utf-8"))
    assert report["status"] == AUTH_MISSING
    assert "credentials" in report["reason"]


def test_check_only_reports_auth_ready_with_fake_client(tmp_path: Path) -> None:
    result = process_private_contact_import(
        task_dir=tmp_path / "in",
        result_dir=tmp_path / "out",
        check_only=True,
        sheets_client=FakeSheetsClient(),
    )

    assert result.status == AUTH_READY


def test_dry_run_reads_private_sheets_without_appending(tmp_path: Path) -> None:
    task_dir = tmp_path / "in"
    task_dir.mkdir()
    _write_task(task_dir)
    client = FakeSheetsClient()

    result = process_private_contact_import(
        task_dir=task_dir,
        result_dir=tmp_path / "out",
        dry_run=True,
        sheets_client=client,
    )

    assert result.status == IMPORT_READY
    assert result.source_rows == 3
    assert result.appended_rows == 0
    assert client.append_calls == []


def test_append_skips_exact_duplicate_rows(tmp_path: Path) -> None:
    task_dir = tmp_path / "in"
    task_dir.mkdir()
    _write_task(task_dir)
    client = FakeSheetsClient()

    result = process_private_contact_import(
        task_dir=task_dir,
        result_dir=tmp_path / "out",
        sheets_client=client,
    )

    assert result.status == IMPORT_READY
    assert result.appended_rows == 1
    assert client.append_calls == [(SHEET_B, "RAW_IMPORT", [["Beta", "private"]])]
    report = json.loads((tmp_path / "out" / "contact-import.result.json").read_text(encoding="utf-8"))
    assert report["status"] == IMPORT_READY
    assert report["source_rows"] == 3
    assert report["appended_rows"] == 1


def test_sheet_access_missing_writes_blocker_without_contact_rows(tmp_path: Path) -> None:
    task_dir = tmp_path / "in"
    task_dir.mkdir()
    _write_task(task_dir, github_status="done")

    result = process_private_contact_import(
        task_dir=task_dir,
        result_dir=tmp_path / "out",
        sheets_client=BlockedSheetsClient(),
    )

    assert result.status == SHEET_ACCESS_MISSING
    assert result.github_status_comment == "blocked"
    report_text = (tmp_path / "out" / "contact-import.result.json").read_text(encoding="utf-8")
    assert "Alpha" not in report_text
    assert "Beta" not in report_text


def test_task_json_rejects_non_allowlisted_contact_payload(tmp_path: Path) -> None:
    task_path = _write_task(tmp_path, rows=[{"name": "not allowed"}])

    with pytest.raises(ContactImportError, match="non-allowlisted fields"):
        _load_task(task_path)


def test_task_json_rejects_invalid_mode(tmp_path: Path) -> None:
    task_path = _write_task(tmp_path, mode="copy_contacts_to_repo")

    with pytest.raises(ContactImportError, match="mode is not allowlisted"):
        _load_task(task_path)


def test_safe_github_status_comment_posts_only_status_word() -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> object:
        calls.append(command)
        return object()

    status = post_safe_github_status_comment(
        ContactImportResult(IMPORT_READY, "task.json", "append", "none", 2, 2, "/private/result.json"),
        repo="alanua/Skeleton",
        issue_number=814,
        run_command=fake_run,  # type: ignore[arg-type]
    )

    assert status == "done"
    assert calls == [
        [
            "gh",
            "issue",
            "comment",
            "814",
            "--repo",
            "alanua/Skeleton",
            "--body",
            "Private contact import: done.",
        ]
    ]
