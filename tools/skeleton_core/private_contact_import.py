from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Protocol


DEFAULT_TASK_DIR = Path("/home/agent/private_runner_inbox/contact_import")
DEFAULT_RESULT_DIR = Path("/home/agent/private_runner_out/contact_import")
DEFAULT_SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)

AUTH_READY = "AUTH_READY"
AUTH_MISSING = "AUTH_MISSING"
AUTH_INVALID = "AUTH_INVALID"
SHEET_ACCESS_MISSING = "SHEET_ACCESS_MISSING"
IMPORT_READY = "IMPORT_READY"
RESULT_STATUSES = frozenset(
    {AUTH_READY, AUTH_MISSING, AUTH_INVALID, SHEET_ACCESS_MISSING, IMPORT_READY}
)
ALLOWED_TASK_KEYS = frozenset(
    {
        "staging_sheet_id",
        "staging_tab",
        "target_sheet_id",
        "target_tab",
        "mode",
        "dedupe_policy",
        "github_status",
    }
)
ALLOWED_MODES = frozenset({"append", "dry_run"})
ALLOWED_DEDUPE_POLICIES = frozenset({"none", "skip_exact_rows"})
SAFE_GITHUB_STATUSES = frozenset({"done", "blocked"})
SHEET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")


class ContactImportError(ValueError):
    """Raised for private contact-import task contract errors."""


class SheetAccessError(RuntimeError):
    """Raised when auth exists but a configured sheet cannot be accessed."""


class SheetsClient(Protocol):
    def read_values(self, sheet_id: str, tab_name: str) -> list[list[object]]:
        """Return all values from a worksheet tab."""

    def append_values(self, sheet_id: str, tab_name: str, values: list[list[object]]) -> None:
        """Append rows to a worksheet tab."""


@dataclass(frozen=True)
class ContactImportTask:
    staging_sheet_id: str
    staging_tab: str
    target_sheet_id: str
    target_tab: str
    mode: str = "append"
    dedupe_policy: str = "skip_exact_rows"
    github_status: str | None = None


@dataclass(frozen=True)
class ContactImportResult:
    status: str
    task: str | None
    mode: str | None
    dedupe_policy: str | None
    source_rows: int
    appended_rows: int
    result_path: str | None
    reason: str | None = None
    github_status_comment: str | None = None

    def compact(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def check_auth(client_factory: Any | None = None) -> tuple[str, str | None]:
    if client_factory is not None:
        try:
            client_factory()
        except FileNotFoundError:
            return AUTH_MISSING, "Google Sheets credentials are not provisioned on this runner."
        except Exception as exc:  # noqa: BLE001 - auth probe preserves safe blocker text only.
            return AUTH_INVALID, f"Google Sheets auth probe failed: {type(exc).__name__}"
        return AUTH_READY, None

    credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if credentials_path and not Path(credentials_path).is_file():
        return AUTH_INVALID, "GOOGLE_APPLICATION_CREDENTIALS points to a missing file."

    try:
        _build_google_sheets_client()
    except FileNotFoundError:
        return AUTH_MISSING, "Google Sheets credentials are not provisioned on this runner."
    except ImportError:
        return AUTH_INVALID, "Google Sheets client libraries are unavailable in this runtime."
    except Exception as exc:  # noqa: BLE001 - never include credential contents in blocker text.
        return AUTH_INVALID, f"Google Sheets auth probe failed: {type(exc).__name__}"
    return AUTH_READY, None


def process_private_contact_import(
    *,
    task_dir: str | Path = DEFAULT_TASK_DIR,
    result_dir: str | Path = DEFAULT_RESULT_DIR,
    task_path: str | Path | None = None,
    check_only: bool = False,
    dry_run: bool = False,
    sheets_client: SheetsClient | None = None,
    client_factory: Any | None = None,
) -> ContactImportResult:
    result_root = Path(result_dir)
    result_root.mkdir(parents=True, exist_ok=True)

    auth_status, auth_reason = check_auth(client_factory if sheets_client is None else (lambda: sheets_client))
    if auth_status != AUTH_READY:
        result = ContactImportResult(auth_status, None, None, None, 0, 0, None, auth_reason)
        return _write_result(result_root / "auth_check_result.json", result)

    if check_only:
        result = ContactImportResult(AUTH_READY, None, None, None, 0, 0, None)
        return _write_result(result_root / "auth_check_result.json", result)

    selected_task_path = Path(task_path) if task_path is not None else _next_task(Path(task_dir))
    if selected_task_path is None:
        result = ContactImportResult(AUTH_READY, None, None, None, 0, 0, None, "No contact import task JSON found.")
        return _write_result(result_root / "auth_check_result.json", result)

    task = _load_task(selected_task_path)
    effective_mode = "dry_run" if dry_run else task.mode
    client = sheets_client if sheets_client is not None else _build_google_sheets_client()

    try:
        source_values = client.read_values(task.staging_sheet_id, task.staging_tab)
        rows_to_append = _rows_to_append(
            source_values,
            client.read_values(task.target_sheet_id, task.target_tab)
            if task.dedupe_policy == "skip_exact_rows"
            else [],
            task.dedupe_policy,
        )
        if effective_mode == "append" and rows_to_append:
            client.append_values(task.target_sheet_id, task.target_tab, rows_to_append)
    except SheetAccessError as exc:
        result = ContactImportResult(
            SHEET_ACCESS_MISSING,
            selected_task_path.name,
            effective_mode,
            task.dedupe_policy,
            0,
            0,
            None,
            str(exc),
            _safe_github_status(task.github_status, blocked=True),
        )
        return _write_task_result(result_root, selected_task_path, result)
    except Exception as exc:  # noqa: BLE001 - keep machine result safe and compact.
        result = ContactImportResult(
            SHEET_ACCESS_MISSING,
            selected_task_path.name,
            effective_mode,
            task.dedupe_policy,
            0,
            0,
            None,
            f"Google Sheets import failed: {type(exc).__name__}",
            _safe_github_status(task.github_status, blocked=True),
        )
        return _write_task_result(result_root, selected_task_path, result)

    result = ContactImportResult(
        IMPORT_READY,
        selected_task_path.name,
        effective_mode,
        task.dedupe_policy,
        len(source_values),
        len(rows_to_append) if effective_mode == "append" else 0,
        None,
        github_status_comment=_safe_github_status(task.github_status, blocked=False),
    )
    return _write_task_result(result_root, selected_task_path, result)


def _load_task(task_path: Path) -> ContactImportTask:
    try:
        payload = json.loads(task_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContactImportError(f"task JSON is invalid: {task_path.name}") from exc
    if not isinstance(payload, dict):
        raise ContactImportError("task JSON must be an object")
    extra = set(payload) - ALLOWED_TASK_KEYS
    if extra:
        raise ContactImportError(f"task JSON has non-allowlisted fields: {', '.join(sorted(extra))}")

    staging_sheet_id = _require_sheet_id(payload, "staging_sheet_id")
    target_sheet_id = _require_sheet_id(payload, "target_sheet_id")
    staging_tab = _require_string(payload, "staging_tab")
    target_tab = _require_string(payload, "target_tab")
    mode = str(payload.get("mode", "append")).strip()
    dedupe_policy = str(payload.get("dedupe_policy", "skip_exact_rows")).strip()
    github_status = payload.get("github_status")
    if mode not in ALLOWED_MODES:
        raise ContactImportError(f"mode is not allowlisted: {mode}")
    if dedupe_policy not in ALLOWED_DEDUPE_POLICIES:
        raise ContactImportError(f"dedupe_policy is not allowlisted: {dedupe_policy}")
    if github_status is not None and github_status not in SAFE_GITHUB_STATUSES:
        raise ContactImportError("github_status may only be done or blocked")
    return ContactImportTask(
        staging_sheet_id=staging_sheet_id,
        staging_tab=staging_tab,
        target_sheet_id=target_sheet_id,
        target_tab=target_tab,
        mode=mode,
        dedupe_policy=dedupe_policy,
        github_status=github_status,
    )


def _require_sheet_id(payload: dict[str, Any], key: str) -> str:
    value = _require_string(payload, key)
    if not SHEET_ID_RE.match(value):
        raise ContactImportError(f"{key} must be a Google Sheet id, not sheet contents")
    return value


def _require_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContactImportError(f"{key} must be a non-empty string")
    return value.strip()


def _rows_to_append(
    source_values: list[list[object]],
    target_values: list[list[object]],
    dedupe_policy: str,
) -> list[list[object]]:
    if dedupe_policy == "none":
        return [list(row) for row in source_values if row]
    existing = {_row_key(row) for row in target_values if row}
    return [list(row) for row in source_values if row and _row_key(row) not in existing]


def _row_key(row: list[object]) -> tuple[str, ...]:
    return tuple("" if cell is None else str(cell) for cell in row)


def _next_task(task_dir: Path) -> Path | None:
    if not task_dir.is_dir():
        return None
    tasks = sorted(path for path in task_dir.iterdir() if path.is_file() and path.suffix == ".json")
    return tasks[0] if tasks else None


def _write_task_result(result_dir: Path, task_path: Path, result: ContactImportResult) -> ContactImportResult:
    destination = result_dir / f"{task_path.stem}.result.json"
    return _write_result(destination, result)


def _write_result(result_path: Path, result: ContactImportResult) -> ContactImportResult:
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_with_path = replace(result, result_path=str(result_path))
    payload = result_with_path.compact()
    result_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return result_with_path


def _safe_github_status(requested: str | None, *, blocked: bool) -> str | None:
    if requested is None:
        return None
    return "blocked" if blocked else "done"


def post_safe_github_status_comment(
    result: ContactImportResult,
    *,
    repo: str,
    issue_number: int,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    status = "done" if result.status == IMPORT_READY else "blocked"
    body = f"Private contact import: {status}."
    run_command(
        ["gh", "issue", "comment", str(issue_number), "--repo", repo, "--body", body],
        check=True,
        text=True,
        capture_output=True,
    )
    return status


def _build_google_sheets_client() -> SheetsClient:
    credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not credentials_path:
        raise FileNotFoundError("GOOGLE_APPLICATION_CREDENTIALS is not set")

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError:
        raise

    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=DEFAULT_SCOPES,
    )
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    return GoogleSheetsClient(service, HttpError)


class GoogleSheetsClient:
    def __init__(self, service: Any, http_error_type: type[BaseException]) -> None:
        self._values = service.spreadsheets().values()
        self._http_error_type = http_error_type

    def read_values(self, sheet_id: str, tab_name: str) -> list[list[object]]:
        try:
            response = self._values.get(spreadsheetId=sheet_id, range=_quote_tab(tab_name)).execute()
        except self._http_error_type as exc:
            raise SheetAccessError(_sheet_access_reason(exc)) from exc
        values = response.get("values", [])
        if not isinstance(values, list):
            raise SheetAccessError("Google Sheets returned invalid values payload.")
        return values

    def append_values(self, sheet_id: str, tab_name: str, values: list[list[object]]) -> None:
        try:
            self._values.append(
                spreadsheetId=sheet_id,
                range=_quote_tab(tab_name),
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": values},
            ).execute()
        except self._http_error_type as exc:
            raise SheetAccessError(_sheet_access_reason(exc)) from exc


def _quote_tab(tab_name: str) -> str:
    return "'" + tab_name.replace("'", "''") + "'"


def _sheet_access_reason(exc: BaseException) -> str:
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status in {401, 403, 404}:
        return "Configured credentials cannot access one or more configured sheets."
    return "Google Sheets API request failed."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Private contact import bootstrap for Google Sheets.")
    parser.add_argument("--task-dir", default=str(DEFAULT_TASK_DIR))
    parser.add_argument("--result-dir", default=str(DEFAULT_RESULT_DIR))
    parser.add_argument("--task-path", default=None)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--post-github-status", action="store_true")
    parser.add_argument("--github-repo", default=None)
    parser.add_argument("--github-issue-number", type=int, default=None)
    args = parser.parse_args(argv)

    try:
        result = process_private_contact_import(
            task_dir=args.task_dir,
            result_dir=args.result_dir,
            task_path=args.task_path,
            check_only=args.check_only,
            dry_run=args.dry_run,
        )
    except ContactImportError as exc:
        result_dir = Path(args.result_dir)
        result_dir.mkdir(parents=True, exist_ok=True)
        result = ContactImportResult(AUTH_INVALID, None, None, None, 0, 0, None, str(exc))
        result = _write_result(result_dir / "task_contract_error.json", result)

    if args.post_github_status:
        if not args.github_repo or args.github_issue_number is None:
            result = ContactImportResult(
                AUTH_INVALID,
                result.task,
                result.mode,
                result.dedupe_policy,
                result.source_rows,
                result.appended_rows,
                result.result_path,
                "--post-github-status requires --github-repo and --github-issue-number",
            )
        else:
            try:
                github_status = post_safe_github_status_comment(
                    result,
                    repo=args.github_repo,
                    issue_number=args.github_issue_number,
                )
                result = replace(result, github_status_comment=github_status)
            except Exception as exc:  # noqa: BLE001 - safe status posting is optional.
                result = replace(result, reason=f"Safe GitHub status comment failed: {type(exc).__name__}")

    print(json.dumps(result.compact(), sort_keys=True, separators=(",", ":")))
    return 0 if result.status in {AUTH_READY, IMPORT_READY} else 2


if __name__ == "__main__":
    raise SystemExit(main())
