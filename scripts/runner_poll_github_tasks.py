from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


REPO = os.environ.get("SKELETON_REPO", "alanua/Skeleton")
LABEL_READY = "runner:ready"
LABEL_DONE = "runner:done"
LABEL_BLOCKED = "runner:blocked"
POLL_INTERVAL = 60
DEFAULT_WORKDIR = Path(__file__).resolve().parents[1]
MAX_COMMENT_LENGTH = 60000
RUNTIME_ARTIFACTS = (
    "core/__pycache__",
    "tests/__pycache__",
    "scripts/__pycache__",
    ".codex",
)


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
            "--json",
            "number,title,body",
        ]
    )
    if code != 0:
        raise RuntimeError(f"gh issue list failed:\n{output}")
    parsed = json.loads(output or "[]")
    if not isinstance(parsed, list):
        raise RuntimeError("gh issue list returned non-list JSON")
    return parsed


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


def block_issue(issue_number: int, message: str) -> None:
    post_issue_comment(issue_number, f"BLOCKED: {message}")
    set_issue_label(issue_number, LABEL_READY, LABEL_BLOCKED)


def process_issue(issue: dict[str, Any], workdir: str | None = None) -> None:
    issue_number = int(issue["number"])
    resolved_workdir = str(Path(workdir) if workdir is not None else DEFAULT_WORKDIR)
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

        branch_code, branch_output, _branch = prepare_issue_branch(
            issue_number, resolved_workdir
        )
        if branch_code != 0:
            block_issue(issue_number, f"Branch preparation failed:\n```\n{branch_output}\n```")
            return

        cleanup_runtime_artifacts(resolved_workdir)
        codex_code, codex_output = run_codex_task(task_content, resolved_workdir)
        cleanup_runtime_artifacts(resolved_workdir)
        if codex_code != 0:
            block_issue(issue_number, f"Codex task failed:\n```\n{codex_output}\n```")
            return

        report = finalize_success(issue, resolved_workdir, codex_output)
        cleanup_runtime_artifacts(resolved_workdir)
        post_issue_comment(issue_number, report)
        set_issue_label(issue_number, LABEL_READY, LABEL_DONE)
    except Exception as exc:
        cleanup_runtime_artifacts(resolved_workdir)
        try:
            block_issue(issue_number, f"Runner error:\n```\n{exc}\n```")
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
