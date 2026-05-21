from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.telegram_approval_buttons import build_pr_ready_card_payload


REPO = os.environ.get("SKELETON_REPO", "alanua/Skeleton")
LABEL_READY = "runner:ready"
LABEL_RUNNING = "runner:running"
LABEL_DONE = "runner:done"
LABEL_BLOCKED = "runner:blocked"
FINAL_LABELS_BY_STATUS = {
    "DONE": LABEL_DONE,
    "BLOCKED": LABEL_BLOCKED,
}
POLL_INTERVAL = 60
DEFAULT_WORKDIR = Path(__file__).resolve().parents[1]
MAX_COMMENT_LENGTH = 60000
RUNTIME_ARTIFACTS = (
    "core/__pycache__",
    "tests/__pycache__",
    "scripts/__pycache__",
    ".codex",
)
TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_TIMEOUT_SECONDS = 10
TELEGRAM_CALLBACK_DATA_LIMIT = 64
TELEGRAM_CARD_TEST_SUMMARY = "Runner pytest completed before draft PR creation."
TELEGRAM_CARD_RISK_SUMMARY = "Review the changed-file list before approval."


def truncate_comment(body: str) -> str:
    if len(body) <= MAX_COMMENT_LENGTH:
        return body
    suffix = "\n\n[Runner output truncated.]"
    return body[: MAX_COMMENT_LENGTH - len(suffix)] + suffix


def cleanup_runtime_artifacts(workdir: str | Path) -> None:
    root = Path(workdir)
    for relative_path in RUNTIME_ARTIFACTS:
        artifact = root / relative_path
        if artifact.is_dir():
            shutil.rmtree(artifact, ignore_errors=True)
        elif artifact.exists():
            artifact.unlink()


def run_command(args: list[str], cwd: str | Path | None = None) -> tuple[int, str]:
    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd is not None else None,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout + result.stderr


def get_ready_issues() -> list[dict[str, Any]]:
    code, output = run_command(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            REPO,
            "--label",
            LABEL_READY,
            "--state",
            "open",
            "--search",
            "is:issue",
            "--json",
            "number,title,body,state,url,closed",
        ]
    )
    if code != 0:
        raise RuntimeError(f"gh issue list failed:\n{output}")
    parsed = json.loads(output or "[]")
    if not isinstance(parsed, list):
        raise RuntimeError("gh issue list returned non-list JSON")
    return [issue for issue in parsed if is_open_task_issue(issue)]


def is_pull_request_item(item: dict[str, Any]) -> bool:
    if item.get("pull_request") is not None:
        return True
    url = str(item.get("url") or item.get("html_url") or "")
    return "/pull/" in url or "/pulls/" in url


def is_open_task_issue(item: dict[str, Any]) -> bool:
    if is_pull_request_item(item):
        return False
    if item.get("closed") is True:
        return False
    state = item.get("state")
    return state is None or str(state).lower() == "open"


def label_names(labels: Any) -> set[str]:
    names: set[str] = set()
    if not isinstance(labels, list):
        return names
    for label in labels:
        if isinstance(label, str):
            names.add(label)
        elif isinstance(label, dict) and isinstance(label.get("name"), str):
            names.add(label["name"])
    return names


def extract_task_block(body: str) -> str | None:
    match = re.search(r"```task\s*\n(?P<task>.*?)\n```", body or "", re.DOTALL)
    if not match:
        return None
    return match.group("task").strip()


def run_codex_task(task_content: str, workdir: str) -> tuple[int, str]:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix="runnerjob-", delete=True
    ) as task_file:
        task_file.write(task_content)
        task_file.flush()
        return run_command(
            [
                "codex",
                "exec",
                "--sandbox",
                "workspace-write",
                "--cd",
                workdir,
                task_content,
            ],
            cwd=workdir,
        )


def post_issue_comment(issue_number: int, body: str) -> None:
    code, output = run_command(
        [
            "gh",
            "issue",
            "comment",
            str(issue_number),
            "--repo",
            REPO,
            "--body",
            truncate_comment(body),
        ]
    )
    if code != 0:
        raise RuntimeError(f"gh issue comment failed:\n{output}")


def set_issue_label(issue_number: int, remove: str, add: str) -> None:
    code, output = run_command(
        [
            "gh",
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            REPO,
            "--remove-label",
            remove,
            "--add-label",
            add,
        ]
    )
    if code != 0:
        raise RuntimeError(f"gh issue edit failed:\n{output}")


def extract_pr_url(report: str) -> str | None:
    match = re.search(r"^Draft PR:\s*(?P<url>\S+)\s*$", report, re.MULTILINE)
    if not match:
        return None
    return match.group("url")


def extract_pr_number(pr_url: str) -> int | None:
    match = re.search(r"/pulls?/(?P<number>[1-9]\d*)(?:/|$)", pr_url)
    if not match:
        return None
    return int(match.group("number"))


def extract_runner_report_pr_binding(
    report: str,
) -> tuple[str | None, tuple[str, ...]]:
    commit = re.search(
        r"^Commit:\s*(?P<sha>[0-9a-fA-F]{40})\s*$", report, re.MULTILINE
    )
    files = re.search(
        r"^Changed files:\s*\n(?P<files>(?:- [^\n]+\n?)+)",
        report,
        re.MULTILINE,
    )
    if not commit or not files:
        return None, ()

    changed_files = tuple(
        line.removeprefix("- ").strip()
        for line in files.group("files").splitlines()
        if line.startswith("- ") and line.removeprefix("- ").strip()
    )
    if not changed_files:
        return None, ()
    return commit.group("sha"), changed_files


def build_telegram_message(
    issue_number: int, status: str, report: str | None = None
) -> str:
    lines = [
        f"Repository: {REPO}",
        f"Issue: #{issue_number}",
        f"Status: {status}",
    ]
    if report:
        pr_url = extract_pr_url(report)
        if pr_url:
            lines.append(f"PR: {pr_url}")
    return "\n".join(lines)


def _telegram_callback_data(button: dict[str, Any]) -> str:
    callback_payload = button.get("callback_payload")
    payload = callback_payload if isinstance(callback_payload, dict) else {}
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    action = re.sub(r"[^a-z_]", "", str(button.get("action") or ""))[:12]
    pr_number = payload.get("pr_number") if isinstance(payload.get("pr_number"), int) else 0
    head_sha = str(payload.get("head_sha") or "").lower()
    head_marker = head_sha[:8] if re.fullmatch(r"[0-9a-f]{40}", head_sha) else "nosha"
    digest = hashlib.sha256(encoded).hexdigest()[:12]
    callback_data = f"tpr1:{action}:p{pr_number}:{head_marker}:{digest}"
    if len(callback_data.encode("utf-8")) > TELEGRAM_CALLBACK_DATA_LIMIT:
        raise ValueError("Telegram callback_data exceeded its bound.")
    return callback_data


def card_payload_to_inline_keyboard(card_payload: dict[str, Any]) -> dict[str, Any]:
    inline_keyboard = []
    for button in card_payload.get("buttons", []):
        if not isinstance(button, dict) or not isinstance(button.get("label"), str):
            continue

        telegram_button = {"text": button["label"]}
        if isinstance(button.get("url"), str):
            telegram_button["url"] = button["url"]
        else:
            telegram_button["callback_data"] = _telegram_callback_data(button)
        inline_keyboard.append([telegram_button])
    return {"inline_keyboard": inline_keyboard}


def _build_details_only_card_payload(pr_url: str, pr_number: int) -> dict[str, Any]:
    callback_base = {"repo": REPO, "pr_number": pr_number, "pr_url": pr_url}
    return {
        "text": "\n".join(
            (
                "PR ready for operator review",
                f"Repository: {REPO}",
                f"PR: #{pr_number}",
                "Approve/reject buttons unavailable: the Runner report lacks a reliable "
                "reviewed SHA or changed-file list.",
            )
        ),
        "buttons": [
            {
                "action": "details",
                "label": "Details",
                "callback_payload": {**callback_base, "action": "details"},
            },
            {
                "action": "open_pr",
                "label": "Open PR",
                "callback_payload": {**callback_base, "action": "open_pr"},
                "url": pr_url,
            },
        ],
    }


def build_done_pr_ready_card_payload(report: str) -> dict[str, Any] | None:
    pr_url = extract_pr_url(report)
    if not pr_url:
        return None

    pr_number = extract_pr_number(pr_url)
    if pr_number is None:
        return None

    head_sha, changed_files = extract_runner_report_pr_binding(report)
    if head_sha is None or not changed_files:
        return _build_details_only_card_payload(pr_url, pr_number)

    try:
        # Runner reports the commit pushed immediately before its draft PR URL;
        # that commit is the reviewed head for this DONE notification.
        return build_pr_ready_card_payload(
            repo=REPO,
            pr_number=pr_number,
            head_sha=head_sha,
            changed_files=changed_files,
            test_summary=TELEGRAM_CARD_TEST_SUMMARY,
            risk_summary=TELEGRAM_CARD_RISK_SUMMARY,
            pr_url=pr_url,
        )
    except ValueError:
        return _build_details_only_card_payload(pr_url, pr_number)


def send_telegram_notification(
    message: str, reply_markup: dict[str, Any] | None = None
) -> None:
    bot_token = os.environ.get("SKELETON_TG_BOT")
    chat_id = os.environ.get("SKELETON_TG_CHAT")
    if not bot_token or not chat_id:
        return

    request_fields = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": "true",
    }
    if reply_markup is not None:
        request_fields["reply_markup"] = json.dumps(
            reply_markup,
            sort_keys=True,
            separators=(",", ":"),
        )
    payload = urllib.parse.urlencode(request_fields).encode("utf-8")
    request = urllib.request.Request(
        f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TELEGRAM_TIMEOUT_SECONDS):
        pass


def get_notification_issue(issue_number: int) -> dict[str, Any]:
    code, output = run_command(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            "--repo",
            REPO,
            "--json",
            "number,body,state,url,closed,labels",
        ]
    )
    if code != 0:
        raise RuntimeError(f"gh issue view failed:\n{output}")
    parsed = json.loads(output or "{}")
    if not isinstance(parsed, dict):
        raise RuntimeError("gh issue view returned non-object JSON")
    return parsed


def should_notify_task_finished(issue_number: int, status: str) -> bool:
    expected_label = FINAL_LABELS_BY_STATUS.get(status)
    if expected_label is None:
        return False

    issue = get_notification_issue(issue_number)
    if not is_open_task_issue(issue):
        return False
    if extract_task_block(issue.get("body") or "") is None:
        return False
    return expected_label in label_names(issue.get("labels"))


def notify_task_finished(
    issue_number: int, status: str, report: str | None = None
) -> None:
    try:
        if not should_notify_task_finished(issue_number, status):
            return
        plain_message = build_telegram_message(issue_number, status, report)
        if status != "DONE" or not report:
            send_telegram_notification(plain_message)
            return

        try:
            card_payload = build_done_pr_ready_card_payload(report)
        except Exception:
            send_telegram_notification(plain_message)
            return

        if card_payload is None:
            send_telegram_notification(plain_message)
            return

        try:
            send_telegram_notification(
                str(card_payload["text"]),
                card_payload_to_inline_keyboard(card_payload),
            )
        except Exception:
            send_telegram_notification(plain_message)
    except Exception:
        return


def ensure_clean_worktree(workdir: str) -> tuple[bool, str]:
    cleanup_runtime_artifacts(workdir)
    code, output = run_command(["git", "status", "--short"], cwd=workdir)
    if code != 0:
        return False, output
    return output.strip() == "", output


def prepare_issue_branch(issue_number: int, workdir: str) -> tuple[int, str, str]:
    branch = f"runner/issue-{issue_number}"
    commands = [
        ["git", "fetch", "origin"],
        ["git", "checkout", "main"],
        ["git", "pull", "origin", "main"],
        ["git", "branch", "-D", branch],
        ["git", "checkout", "-b", branch, "origin/main"],
    ]
    combined_output = []
    for command in commands:
        code, output = run_command(command, cwd=workdir)
        combined_output.append(f"$ {' '.join(command)}\n{output}")
        if code != 0 and command[:3] != ["git", "branch", "-D"]:
            return code, "\n".join(combined_output), branch
    return 0, "\n".join(combined_output), branch


def changed_files(workdir: str) -> list[str]:
    cleanup_runtime_artifacts(workdir)
    files: set[str] = set()
    for command in (
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        code, output = run_command(command, cwd=workdir)
        if code != 0:
            raise RuntimeError(f"{' '.join(command)} failed:\n{output}")
        files.update(line.strip() for line in output.splitlines() if line.strip())
    return sorted(files)


def finalize_success(issue: dict[str, Any], workdir: str, codex_output: str) -> str:
    issue_number = int(issue["number"])
    files = changed_files(workdir)
    if not files:
        cleanup_runtime_artifacts(workdir)
        return (
            "DONE: Codex completed successfully with no file changes.\n\n"
            "Runtime artifacts cleaned after Codex execution.\n\n"
            f"Codex output:\n```\n{codex_output.strip()}\n```"
        )

    checks: list[tuple[str, str]] = []

    for command in (
        ["git", "diff", "--check"],
        ["python3", "-m", "pytest", "-q"],
    ):
        code, output = run_command(command, cwd=workdir)
        checks.append((" ".join(command), output))
        if code != 0:
            raise RuntimeError(f"{' '.join(command)} failed:\n{output}")

    cleanup_runtime_artifacts(workdir)
    files = changed_files(workdir)

    for command in (
        ["git", "add", *files],
        ["git", "diff", "--cached", "--check"],
        ["git", "commit", "-m", f"runner: issue #{issue_number} task"],
        [
            "git",
            "push",
            "--force-with-lease",
            "-u",
            "origin",
            f"runner/issue-{issue_number}",
        ],
    ):
        code, output = run_command(command, cwd=workdir)
        checks.append((" ".join(command), output))
        if code != 0:
            raise RuntimeError(f"{' '.join(command)} failed:\n{output}")

    cleanup_runtime_artifacts(workdir)

    code, commit_sha = run_command(["git", "rev-parse", "HEAD"], cwd=workdir)
    if code != 0:
        raise RuntimeError(f"git rev-parse HEAD failed:\n{commit_sha}")
    commit_sha = commit_sha.strip()

    pr_title = f"Runner task #{issue_number}: {issue.get('title', '').strip()}"
    pr_body = f"Automated Runner task from issue #{issue_number}."
    pr_command = [
        "gh",
        "pr",
        "create",
        "--repo",
        REPO,
        "--base",
        "main",
        "--head",
        f"runner/issue-{issue_number}",
        "--title",
        pr_title,
        "--body",
        pr_body,
        "--draft",
    ]
    pr_code, pr_output = run_command(pr_command, cwd=workdir)
    if pr_code == 0:
        pr_url = pr_output.strip()
    else:
        view_code, view_output = run_command(
            [
                "gh",
                "pr",
                "view",
                f"runner/issue-{issue_number}",
                "--repo",
                REPO,
                "--json",
                "url",
                "--jq",
                ".url",
            ],
            cwd=workdir,
        )
        if view_code != 0:
            raise RuntimeError(
                f"gh pr create failed:\n{pr_output}\n\ngh pr view failed:\n{view_output}"
            )
        pr_url = view_output.strip()

    pytest_output = next(
        output for command, output in checks if command == "python3 -m pytest -q"
    )
    return (
        "DONE: Codex completed successfully and produced file changes.\n\n"
        "Changed files:\n"
        + "\n".join(f"- {file_name}" for file_name in files)
        + "\n\n"
        f"Pytest output:\n```\n{pytest_output.strip()}\n```\n\n"
        f"Commit: {commit_sha}\n"
        f"Draft PR: {pr_url}"
    )


def block_issue(issue_number: int, message: str, remove_label: str = LABEL_READY) -> None:
    post_issue_comment(issue_number, f"BLOCKED: {message}")
    set_issue_label(issue_number, remove_label, LABEL_BLOCKED)
    notify_task_finished(issue_number, "BLOCKED")


def process_issue(issue: dict[str, Any], workdir: str | None = None) -> None:
    issue_number = int(issue["number"])
    if not is_open_task_issue(issue):
        return

    resolved_workdir = str(Path(workdir) if workdir is not None else DEFAULT_WORKDIR)
    claimed = False
    try:
        task_content = extract_task_block(issue.get("body") or "")
        if task_content is None:
            block_issue(
                issue_number,
                "No fenced task block found. Add a fence that starts with ```task.",
            )
            return

        clean, status_output = ensure_clean_worktree(resolved_workdir)
        if not clean:
            block_issue(
                issue_number,
                "Runner worktree is not clean before starting.\n\n"
                f"git status --short:\n```\n{status_output.strip()}\n```",
            )
            return

        set_issue_label(issue_number, LABEL_READY, LABEL_RUNNING)
        claimed = True

        branch_code, branch_output, _branch = prepare_issue_branch(
            issue_number, resolved_workdir
        )
        if branch_code != 0:
            block_issue(
                issue_number,
                f"Branch preparation failed:\n```\n{branch_output}\n```",
                remove_label=LABEL_RUNNING,
            )
            return

        cleanup_runtime_artifacts(resolved_workdir)
        codex_code, codex_output = run_codex_task(task_content, resolved_workdir)
        cleanup_runtime_artifacts(resolved_workdir)
        if codex_code != 0:
            block_issue(
                issue_number,
                f"Codex task failed:\n```\n{codex_output}\n```",
                remove_label=LABEL_RUNNING,
            )
            return

        report = finalize_success(issue, resolved_workdir, codex_output)
        cleanup_runtime_artifacts(resolved_workdir)
        post_issue_comment(issue_number, report)
        set_issue_label(issue_number, LABEL_RUNNING, LABEL_DONE)
        notify_task_finished(issue_number, "DONE", report)
    except Exception as exc:
        cleanup_runtime_artifacts(resolved_workdir)
        try:
            remove_label = LABEL_RUNNING if claimed else LABEL_READY
            block_issue(
                issue_number,
                f"Runner error:\n```\n{exc}\n```",
                remove_label=remove_label,
            )
        except Exception:
            return


def poll_once(workdir: str | None = None) -> int:
    issues = get_ready_issues()
    for issue in issues:
        process_issue(issue, workdir=workdir)
    return len(issues)


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll GitHub runner task issues.")
    parser.add_argument("--loop", action="store_true", help="poll continuously")
    parser.add_argument("--workdir", default=None, help="repository workdir")
    args = parser.parse_args()

    if args.loop:
        while True:
            poll_once(workdir=args.workdir)
            time.sleep(POLL_INTERVAL)
    else:
        poll_once(workdir=args.workdir)


if __name__ == "__main__":
    main()
