from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
import re
import shlex
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

from core.project_tree import get_project, get_project_by_repo, load_project_tree
from core.telegram_approval_buttons import build_pr_ready_card_payload


QUEUE_REPOSITORY = "alanua/Skeleton"
REPO = QUEUE_REPOSITORY
LABEL_READY = "runner:ready"
LABEL_RUNNING = "runner:running"
LABEL_DONE = "runner:done"
LABEL_BLOCKED = "runner:blocked"
RUNNER_LANE_LABELS = {
    "default": "runner:lane:default",
    "lane-1": "runner:lane:lane-1",
    "lane-2": "runner:lane:lane-2",
}
RUNNER_LANE_LABEL_DESCRIPTIONS = {
    "default": "Visible default Runner lane marker",
    "lane-1": "Visible Runner lane-1 marker",
    "lane-2": "Visible Runner lane-2 marker",
}
FINAL_LABELS_BY_STATUS = {
    "DONE": LABEL_DONE,
    "BLOCKED": LABEL_BLOCKED,
}
POLL_INTERVAL = 60
DEFAULT_WORKDIR = Path(__file__).resolve().parents[1]
DEFAULT_WORKTREE_ROOT = Path("/home/agent/agent-dev/worktrees/skeleton")
PROJECT_TREE_PATH = ROOT / "PROJECT_TREE.yaml"
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
TELEGRAM_CALLBACK_HMAC_ENV = "SKELETON_TG_CALLBACK_HMAC_SECRET"
TELEGRAM_CARD_TEST_SUMMARY = "Runner pytest completed before draft PR creation."
TELEGRAM_CARD_RISK_SUMMARY = "Review the changed-file list before approval."
TELEGRAM_PR_READY_BUTTON_LABELS = {
    "approve": "Схвалити",
    "reject": "Відхилити",
    "details": "Деталі",
    "open_pr": "Відкрити PR",
}
RUNTIME_MAINTENANCE_MODE = "RUNTIME_MAINTENANCE_TASK"
SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME = "sync_telegram_callback_poller_runtime"
ENSURE_TELEGRAM_CALLBACK_LOCAL_CONFIG = "ensure_telegram_callback_local_config"
CHECK_PROJECT_CHECKOUT = "check_project_checkout"
ENSURE_PROJECT_CHECKOUT = "ensure_project_checkout"
VALIDATE_PR_BRANCH = "validate_pr_branch"
INSPECT_PR_MERGEABILITY = "inspect_pr_mergeability"
RUNTIME_MAINTENANCE_TASK_IDS = frozenset(
    (
        SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME,
        ENSURE_TELEGRAM_CALLBACK_LOCAL_CONFIG,
        CHECK_PROJECT_CHECKOUT,
        ENSURE_PROJECT_CHECKOUT,
        VALIDATE_PR_BRANCH,
        INSPECT_PR_MERGEABILITY,
    )
)
RUNNER_PROJECT_CHECKOUT_BASE = Path("/home/agent/agent-dev")
PR_BRANCH_VALIDATION_WORKTREE_DIR = "validate-pr-branch"
PR_BRANCH_VALIDATION_PROFILES = {
    "full_pytest": (("python3", "-m", "pytest", "-q"),),
    "knowledge_intake": (
        ("python3", "-m", "pytest", "-q", "tests/test_knowledge_intake.py"),
        ("python3", "-m", "pytest", "-q"),
    ),
}
VALIDATION_FAILED_OUTPUT_LIMIT = 4000
VALIDATION_FAILED_OUTPUT_TRUNCATED_MARKER = (
    "[Runner validation output truncated to 4000 characters.]"
)
TELEGRAM_APPROVED_PR_MERGE_MODE = "TELEGRAM_APPROVED_PR_MERGE"
TELEGRAM_APPROVED_PR_MERGE_ACTION = "squash"
TELEGRAM_CALLBACK_POLLER_SERVICE = "skeleton-telegram-callback-poll.service"
TELEGRAM_CALLBACK_POLLER_TIMER = "skeleton-telegram-callback-poll.timer"
TELEGRAM_CALLBACK_LOCAL_CONFIG = "/etc/skeleton-runner.env"
TELEGRAM_CALLBACK_POLLER_RUNTIME_FILES = (
    "scripts/telegram_callback_poller.py",
    f"scripts/{TELEGRAM_CALLBACK_POLLER_SERVICE}",
    f"scripts/{TELEGRAM_CALLBACK_POLLER_TIMER}",
)

_HEAD_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_CALLBACK_DIGEST_RE = re.compile(r"^[0-9a-f]{12}$")
_BLOCKED_OUTPUT_MARKERS = (
    "BLOCKED",
    "Blocked:",
    "missing capability",
    "wrong worktree",
    "not target repo",
    "writer unavailable",
    "cancelled",
    "no build files",
    "PlatformIO not available",
    "no firmware",
    "assigned worktree is not target",
)
_BLOCKED_OUTPUT_MARKER_RES = tuple(
    re.compile(rf"(?<!\w){re.escape(marker)}(?!\w)", re.IGNORECASE)
    for marker in _BLOCKED_OUTPUT_MARKERS
)
_FINAL_STATUS_LINE_RE = re.compile(r"^\s*(DONE|BLOCKED)\b:?", re.IGNORECASE)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ENV_ASSIGNMENT_LINE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{1,80}=.*$")
_SENSITIVE_OUTPUT_VALUE_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASS|KEY|CREDENTIAL|AUTH)"
    r"[A-Z0-9_]*)\s*=\s*([^\s]+)"
)


@dataclass(frozen=True)
class RunnerLane:
    name: str


DEFAULT_RUNNER_LANE = RunnerLane("default")
ALLOWED_RUNNER_LANES = frozenset(RUNNER_LANE_LABELS)


def load_runner_project_tree() -> dict[str, Any]:
    return load_project_tree(PROJECT_TREE_PATH)


def allowed_target_repositories() -> frozenset[str]:
    project_tree = load_runner_project_tree()
    return frozenset(
        project["repo"]
        for project in project_tree["projects"].values()
        if project["public"] is True
    )


ALLOWED_TARGET_REPOSITORIES = allowed_target_repositories()


def allowed_target_projects() -> frozenset[str]:
    project_tree = load_runner_project_tree()
    return frozenset(
        project_id
        for project_id, project in project_tree["projects"].items()
        if project["public"] is True
    )


ALLOWED_TARGET_PROJECTS = allowed_target_projects()


@dataclass(frozen=True)
class RunnerTask:
    content: str
    lane: RunnerLane = DEFAULT_RUNNER_LANE
    has_lane_metadata: bool = False
    target_project: str = "skeleton"
    has_target_project_metadata: bool = False
    target_repository: str = QUEUE_REPOSITORY
    has_target_repository_metadata: bool = False


@dataclass(frozen=True)
class TelegramApprovedPrMergeRequest:
    pr_number: int
    approved_head_sha: str
    callback_digest: str
    action: str = TELEGRAM_APPROVED_PR_MERGE_ACTION


@dataclass(frozen=True)
class RegisteredProjectCheckout:
    target_project: str
    repo: str
    checkout_path_text: str
    checkout_path: Path
    status_lines: list[str]


@dataclass(frozen=True)
class PrBranchValidationRequest:
    pr_number: int
    expected_head_sha: str | None
    profile: str


@dataclass(frozen=True)
class PrMergeabilityInspectionRequest:
    pr_number: int
    expected_head_sha: str | None


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


def worktree_root() -> Path:
    configured_root = os.environ.get("SKELETON_WORKTREE_ROOT")
    if configured_root:
        return Path(configured_root).expanduser()
    return DEFAULT_WORKTREE_ROOT


def issue_worktree_path(issue_number: int) -> Path:
    return worktree_root() / f"issue-{issue_number}"


def target_repository_worktree_root(target_repository: str) -> Path:
    try:
        project = get_project_by_repo(load_runner_project_tree(), target_repository)
    except KeyError as exc:
        allowed = ", ".join(f"`{repo}`" for repo in sorted(ALLOWED_TARGET_REPOSITORIES))
        raise ValueError(
            f"Target repository `{target_repository}` is not allowlisted. Use {allowed}."
        ) from exc
    if target_repository == QUEUE_REPOSITORY:
        return worktree_root()
    return Path(project["worktree_root"])


def target_repository_checkout_path(target_repository: str) -> Path:
    try:
        project = get_project_by_repo(load_runner_project_tree(), target_repository)
    except KeyError as exc:
        allowed = ", ".join(f"`{repo}`" for repo in sorted(ALLOWED_TARGET_REPOSITORIES))
        raise ValueError(
            f"Target repository `{target_repository}` is not allowlisted. Use {allowed}."
        ) from exc
    return Path(project["checkout_path"])


def target_repository_issue_worktree_path(
    target_repository: str, issue_number: int
) -> Path:
    return target_repository_worktree_root(target_repository) / f"issue-{issue_number}"


def ensure_safe_target_repository_worktree_path(
    target_repository: str, path: str | Path
) -> Path:
    root = target_repository_worktree_root(target_repository).resolve()
    candidate = Path(path).expanduser().resolve()
    if candidate == root:
        raise ValueError(f"Refusing to use worktree root as issue worktree: {candidate}")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Refusing worktree path outside configured root {root}: {candidate}"
        ) from exc
    return candidate


def ensure_safe_worktree_path(path: str | Path) -> Path:
    return ensure_safe_target_repository_worktree_path(QUEUE_REPOSITORY, path)


def issue_branch(issue_number: int) -> str:
    return f"runner/issue-{issue_number}"


def format_command_output(command: list[str], output: str) -> str:
    return f"$ {' '.join(command)}\n{output}"


def prepare_issue_worktree(
    issue_number: int, coordinator_workdir: str | Path
) -> tuple[int, str, Path]:
    path = ensure_safe_worktree_path(issue_worktree_path(issue_number))
    return prepare_git_issue_worktree(issue_number, coordinator_workdir, path)


def prepare_target_repository_issue_worktree(
    target_repository: str, issue_number: int
) -> tuple[int, str, Path]:
    path = ensure_safe_target_repository_worktree_path(
        target_repository,
        target_repository_issue_worktree_path(target_repository, issue_number),
    )
    checkout_path = target_repository_checkout_path(target_repository)
    return prepare_git_issue_worktree(issue_number, checkout_path, path)


def prepare_git_issue_worktree(
    issue_number: int, coordinator_workdir: str | Path, path: Path
) -> tuple[int, str, Path]:
    branch = issue_branch(issue_number)
    outputs: list[str] = []

    if path.exists():
        checks = (
            (["git", "status", "--short"], "dirty"),
            (["git", "branch", "--show-current"], "branch"),
        )
        for command, check_name in checks:
            code, output = run_command(command, cwd=path)
            outputs.append(format_command_output(command, output))
            if code != 0:
                return (
                    code,
                    "Existing issue worktree needs cleanup before reuse.\n\n"
                    + "\n".join(outputs),
                    path,
                )
            if check_name == "dirty" and output.strip():
                return (
                    1,
                    "Existing issue worktree is dirty; cleanup is required before reuse.\n\n"
                    + "\n".join(outputs),
                    path,
                )
            if check_name == "branch" and output.strip() != branch:
                return (
                    1,
                    "Existing issue worktree is on the wrong branch; cleanup is "
                    f"required before reuse. Expected {branch!r}.\n\n"
                    + "\n".join(outputs),
                    path,
                )
        return 0, "\n".join(outputs), path

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return 1, f"Unable to create worktree root {path.parent}:\n{exc}", path

    for command in (
        ["git", "fetch", "origin"],
        ["git", "worktree", "add", "-B", branch, str(path), "origin/main"],
    ):
        code, output = run_command(command, cwd=coordinator_workdir)
        outputs.append(format_command_output(command, output))
        if code != 0:
            return code, "\n".join(outputs), path
    return 0, "\n".join(outputs), path


def prepare_issue_branch(
    issue_number: int, coordinator_workdir: str | Path
) -> tuple[int, str, Path]:
    return prepare_issue_worktree(issue_number, coordinator_workdir)


def cleanup_git_issue_worktree(path: Path, coordinator_workdir: str | Path) -> tuple[int, str]:
    if not path.exists():
        return 0, ""
    cleanup_runtime_artifacts(path)
    outputs: list[str] = []
    for command in (
        ["git", "worktree", "remove", "--force", str(path)],
        ["git", "worktree", "prune"],
    ):
        code, output = run_command(command, cwd=coordinator_workdir)
        outputs.append(format_command_output(command, output))
        if code != 0:
            return code, "\n".join(outputs)
    return 0, "\n".join(outputs)


def cleanup_issue_worktree(
    issue_number: int, coordinator_workdir: str | Path
) -> tuple[int, str]:
    try:
        path = ensure_safe_worktree_path(issue_worktree_path(issue_number))
    except ValueError as exc:
        return 1, str(exc)
    return cleanup_git_issue_worktree(path, coordinator_workdir)


def cleanup_target_repository_issue_worktree(
    target_repository: str, issue_number: int
) -> tuple[int, str]:
    try:
        path = ensure_safe_target_repository_worktree_path(
            target_repository,
            target_repository_issue_worktree_path(target_repository, issue_number),
        )
    except ValueError as exc:
        return 1, str(exc)
    return cleanup_git_issue_worktree(path, target_repository_checkout_path(target_repository))


def issue_workspace_review_note(path: str | Path) -> str:
    return f"\n\nIssue workspace kept for review:\n`{path}`"


def report_runner_lane(report: str, task: RunnerTask | None) -> str:
    if (
        task is None
        or not task.has_lane_metadata
        and not task.has_target_project_metadata
        and not task.has_target_repository_metadata
    ):
        return report
    heading, *details = report.splitlines()
    metadata: list[str] = []
    if task.has_lane_metadata:
        metadata.append(f"Runner Lane: {task.lane.name}")
    if task.has_target_project_metadata:
        metadata.append(f"Target Project: {task.target_project}")
    if task.has_target_repository_metadata:
        metadata.append(f"Target Repository: {task.target_repository}")
    return "\n".join((heading, *metadata, *details))


def final_codex_answer(output: str) -> str:
    """Return the final Codex answer without echoed prompt/transcript text."""
    text = _ANSI_ESCAPE_RE.sub("", output or "")
    boundaries = (
        "\nReading additional input from stdin...",
        "\nOpenAI Codex v",
        "\n--------\nworkdir:",
    )
    lines = text.splitlines(keepends=True)
    in_fence = False
    final_status_index: int | None = None
    offset = 0
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and _FINAL_STATUS_LINE_RE.match(line):
            final_status_index = offset
        offset += len(line)
    if final_status_index is not None:
        text = text[final_status_index:]

    cut = len(text)
    for boundary in boundaries:
        index = text.find(boundary)
        if index != -1:
            cut = min(cut, index)
    return text[:cut].strip()


def _without_fenced_blocks(text: str) -> str:
    kept: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            kept.append(line)
    return "\n".join(kept)


def blocked_output_marker(output: str) -> str | None:
    final_answer = _without_fenced_blocks(final_codex_answer(output))
    for marker, marker_re in zip(_BLOCKED_OUTPUT_MARKERS, _BLOCKED_OUTPUT_MARKER_RES):
        if marker_re.search(final_answer):
            return marker
    return None


def runner_report_status(report: str) -> str:
    if blocked_output_marker(report):
        return "BLOCKED"
    if re.match(r"^DONE\b:?", report or "") is None:
        return "BLOCKED"
    if (
        "Codex completed successfully and produced file changes." in report
        and not extract_pr_url(report)
    ):
        return "BLOCKED"
    return "DONE"


def blocked_final_report(report: str) -> str:
    marker = blocked_output_marker(report)
    if marker is not None:
        reason = f"blocked marker `{marker}` was present"
    elif (
        "Codex completed successfully and produced file changes." in report
        and not extract_pr_url(report)
    ):
        reason = "draft PR URL was missing from a file-change report"
    else:
        reason = "runner report did not meet completion criteria"
    return (
        "BLOCKED: Runner did not mark this task complete.\n\n"
        f"Reason: {reason}.\n\n"
        f"Runner report:\n```\n{report.strip()}\n```"
    )


def blocked_codex_output_report(
    codex_output: str,
    marker: str,
    issue_workdir: str,
) -> str:
    return (
        "BLOCKED: Codex output reported a blocked deliverable.\n\n"
        f"Blocked marker: {marker}\n\n"
        f"Codex output:\n```\n{codex_output.strip()}\n```"
        + issue_workspace_review_note(issue_workdir)
    )


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


def extract_runner_lane(body: str) -> tuple[RunnerLane | None, str | None]:
    metadata = (body or "").split("```task", 1)[0]
    lane_name = _body_field(metadata, "Runner Lane") or _body_field(metadata, "Lane")
    if lane_name is None:
        return DEFAULT_RUNNER_LANE, None
    if lane_name not in ALLOWED_RUNNER_LANES:
        allowed = ", ".join(f"`{name}`" for name in sorted(ALLOWED_RUNNER_LANES))
        return None, f"Runner lane `{lane_name}` is not allowlisted. Use {allowed}."
    return RunnerLane(lane_name), None


def _project_id_for_repo(project_tree: dict[str, Any], repo: str) -> str:
    for project_id, project in project_tree["projects"].items():
        if project["repo"] == repo:
            return project_id
    raise KeyError(f"unknown repo {repo!r}.")


def resolve_target_project_metadata(
    body: str,
) -> tuple[str | None, str | None, str | None]:
    metadata = (body or "").split("```task", 1)[0]
    target_project = _body_field(metadata, "Target Project")
    target_repository = _body_field(metadata, "Target Repository")
    project_tree = load_runner_project_tree()

    if target_project is None and target_repository is None:
        return "skeleton", QUEUE_REPOSITORY, None

    project_from_project: dict[str, Any] | None = None
    project_from_repository: dict[str, Any] | None = None
    project_id_from_repository: str | None = None

    if target_project is not None:
        try:
            project_from_project = get_project(project_tree, target_project)
        except (KeyError, ValueError) as exc:
            allowed = ", ".join(
                f"`{project}`" for project in sorted(ALLOWED_TARGET_PROJECTS)
            )
            return (
                None,
                None,
                f"Target project `{target_project}` is not allowlisted. Use {allowed}.",
            )
        if project_from_project["public"] is not True:
            return None, None, f"Target project `{target_project}` is not public."

    if target_repository is not None:
        try:
            project_from_repository = get_project_by_repo(project_tree, target_repository)
            project_id_from_repository = _project_id_for_repo(
                project_tree, target_repository
            )
        except KeyError:
            allowed = ", ".join(
                f"`{repo}`" for repo in sorted(ALLOWED_TARGET_REPOSITORIES)
            )
            return (
                None,
                None,
                f"Target repository `{target_repository}` is not allowlisted. Use {allowed}.",
            )
        if project_from_repository["public"] is not True:
            return None, None, f"Target repository `{target_repository}` is not public."

    if target_project is not None and target_repository is not None:
        if project_from_project != project_from_repository:
            return (
                None,
                None,
                "Target Project and Target Repository resolve to different "
                "PROJECT_TREE entries.",
            )
        return target_project, target_repository, None

    if target_project is not None and project_from_project is not None:
        return target_project, project_from_project["repo"], None

    if project_id_from_repository is not None and target_repository is not None:
        return project_id_from_repository, target_repository, None

    return None, None, "Target project metadata could not be resolved."


def extract_target_repository(body: str) -> tuple[str | None, str | None]:
    _target_project, target_repository, reason = resolve_target_project_metadata(body)
    return target_repository, reason


def extract_runner_task(body: str) -> tuple[RunnerTask | None, str | None]:
    content = extract_task_block(body)
    if content is None:
        return None, None
    metadata = (body or "").split("```task", 1)[0]
    lane, lane_reason = extract_runner_lane(body)
    if lane is None:
        return None, lane_reason
    target_project, target_repository, target_reason = resolve_target_project_metadata(
        body
    )
    if target_project is None or target_repository is None:
        return None, target_reason
    return RunnerTask(
        content=content,
        lane=lane,
        has_lane_metadata=(
            _body_field(metadata, "Runner Lane") is not None
            or _body_field(metadata, "Lane") is not None
        ),
        target_project=target_project,
        has_target_project_metadata=(
            _body_field(metadata, "Target Project") is not None
        ),
        target_repository=target_repository,
        has_target_repository_metadata=(
            _body_field(metadata, "Target Repository") is not None
        ),
    ), None


def project_execution_block_reason(task: RunnerTask) -> str | None:
    project = get_project(load_runner_project_tree(), task.target_project)
    execution_modes = project.get("execution_modes") or {}

    if project.get("runner_enabled") is not True:
        return f"Runner is disabled for target project `{task.target_project}`."
    if execution_modes.get("planning_only") is True:
        return (
            f"Target project `{task.target_project}` is planning-only. "
            "Runner will not execute Codex for this project."
        )
    if task.target_repository == QUEUE_REPOSITORY:
        if execution_modes.get("codex_issue_worktree") is True:
            return None
        return (
            f"Target project `{task.target_project}` does not enable "
            "codex issue worktree execution."
        )
    if execution_modes.get("live_cross_repo") is True:
        return (
            "Live cross-repo execution is blocked in this runner stage and "
            "requires a separate PR."
        )
    if execution_modes.get("codex_issue_worktree") is True:
        return None
    return (
        f"Target project `{task.target_project}` does not enable an executable "
        "runner mode."
    )


def extract_runtime_maintenance_task_id(body: str) -> tuple[bool, str | None]:
    mode_found = re.search(
        rf"^\s*Mode:\s*{RUNTIME_MAINTENANCE_MODE}\s*$",
        body or "",
        re.MULTILINE,
    )
    if mode_found is None:
        return False, None

    task_id = re.search(
        r"^\s*Maintenance Task ID:\s*(?P<task_id>[a-z0-9_]+)\s*$",
        body or "",
        re.MULTILINE,
    )
    return True, task_id.group("task_id") if task_id else None


def extract_telegram_approved_pr_merge_request(
    body: str,
) -> tuple[bool, TelegramApprovedPrMergeRequest | None, str | None]:
    mode_found = re.search(
        rf"^\s*Mode:\s*{TELEGRAM_APPROVED_PR_MERGE_MODE}\s*$",
        body or "",
        re.MULTILINE,
    )
    if mode_found is None:
        return False, None, None

    repo = _body_field(body, "Repository")
    pr_number = _body_field(body, "Pull Request")
    head_sha = _body_field(body, "Approved Head SHA")
    action = _body_field(body, "Merge Action")
    approval_source = _body_field(body, "Approval Source")
    callback_digest = _body_field(body, "Callback Digest")
    if repo != REPO:
        return True, None, f"Telegram approved merge repository must be `{REPO}`."
    if not isinstance(pr_number, str) or not re.fullmatch(r"[1-9]\d*", pr_number):
        return True, None, "Telegram approved merge pull request is malformed."
    if not isinstance(head_sha, str) or _HEAD_SHA_RE.fullmatch(head_sha) is None:
        return True, None, "Telegram approved merge head SHA is malformed."
    if action != TELEGRAM_APPROVED_PR_MERGE_ACTION:
        return True, None, "Telegram approved merge action must be squash."
    if approval_source != "signed_telegram_callback":
        return True, None, "Telegram approved merge source is not allowlisted."
    if (
        not isinstance(callback_digest, str)
        or _CALLBACK_DIGEST_RE.fullmatch(callback_digest) is None
    ):
        return True, None, "Telegram approved merge callback digest is malformed."
    return (
        True,
        TelegramApprovedPrMergeRequest(
            pr_number=int(pr_number),
            approved_head_sha=head_sha.lower(),
            callback_digest=callback_digest,
        ),
        None,
    )


def _body_field(body: str, field: str) -> str | None:
    match = re.search(
        rf"^\s*{re.escape(field)}:\s*(?P<value>\S(?:.*\S)?)\s*$",
        body or "",
        re.MULTILINE,
    )
    return match.group("value") if match else None


def has_runner_task_body(body: str) -> bool:
    maintenance_mode, _task_id = extract_runtime_maintenance_task_id(body)
    merge_mode, _request, _reason = extract_telegram_approved_pr_merge_request(body)
    return extract_task_block(body) is not None or maintenance_mode or merge_mode


def build_codex_task_prompt(
    task_content: str, workdir: str, task: RunnerTask | None = None
) -> str:
    selected_project_context = ""
    if task is not None:
        selected_project_context = (
            f"Selected Project: {task.target_project}\n"
            f"Selected Repository: {task.target_repository}\n\n"
        )
    return (
        "Runner assigned this task to the issue worktree at:\n"
        f"{workdir}\n\n"
        "Edit files only inside that issue worktree. Do not create or use a separate "
        "clone, checkout, or worktree for task output.\n\n"
        f"{selected_project_context}"
        f"{task_content}"
    )


def run_codex_task(
    task_content: str, workdir: str, task: RunnerTask | None = None
) -> tuple[int, str]:
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
                build_codex_task_prompt(task_content, workdir, task),
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


def apply_runner_lane_label(issue_number: int, task: RunnerTask | None) -> None:
    if task is None or not task.has_lane_metadata:
        return

    label = ensure_runner_lane_label(task.lane)
    code, output = run_command(
        [
            "gh",
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            REPO,
            "--add-label",
            label,
        ]
    )
    if code != 0:
        raise RuntimeError(f"gh issue lane label edit failed:\n{output}")


def ensure_runner_lane_label(lane: RunnerLane) -> str:
    label = RUNNER_LANE_LABELS.get(lane.name)
    if label is None:
        raise ValueError(f"Refusing to create non-allowlisted Runner lane `{lane.name}`.")

    code, output = run_command(
        [
            "gh",
            "label",
            "list",
            "--repo",
            REPO,
            "--search",
            label,
            "--json",
            "name",
        ]
    )
    if code != 0:
        raise RuntimeError(f"gh runner lane label list failed:\n{output}")

    try:
        existing_labels = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh runner lane label list returned invalid JSON:\n{output}") from exc

    if any(
        isinstance(existing_label, dict) and existing_label.get("name") == label
        for existing_label in existing_labels
    ):
        return label

    code, output = run_command(
        [
            "gh",
            "label",
            "create",
            label,
            "--repo",
            REPO,
            "--description",
            RUNNER_LANE_LABEL_DESCRIPTIONS[lane.name],
        ]
    )
    if code != 0:
        raise RuntimeError(f"gh runner lane label create failed:\n{output}")
    return label


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
    hmac_secret = os.environ.get(TELEGRAM_CALLBACK_HMAC_ENV)
    digest = (
        hmac.new(
            hmac_secret.encode("utf-8"),
            f"tpr1:{action}:p{pr_number}:{head_marker}".encode("ascii"),
            hashlib.sha256,
        ).hexdigest()[:12]
        if hmac_secret
        else hashlib.sha256(encoded).hexdigest()[:12]
    )
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


def _build_pr_ready_operator_text(pr_number: int) -> str:
    return "\n".join(
        (
            f"PR: #{pr_number}",
            "Надішліть номер PR у ChatGPT.",
            "Натисніть «Схвалити» лише після того, як ChatGPT скаже схвалити.",
        )
    )


def _localize_pr_ready_card_payload(
    card_payload: dict[str, Any], pr_number: int
) -> dict[str, Any]:
    buttons = []
    for button in card_payload.get("buttons", []):
        if not isinstance(button, dict):
            continue
        action = str(button.get("action") or "")
        buttons.append(
            {
                **button,
                "label": TELEGRAM_PR_READY_BUTTON_LABELS.get(
                    action, str(button.get("label") or "")
                ),
            }
        )
    return {
        **card_payload,
        "text": _build_pr_ready_operator_text(pr_number),
        "buttons": buttons,
    }


def _build_details_only_card_payload(pr_url: str, pr_number: int) -> dict[str, Any]:
    callback_base = {"repo": REPO, "pr_number": pr_number, "pr_url": pr_url}
    return {
        "text": _build_pr_ready_operator_text(pr_number),
        "buttons": [
            {
                "action": "details",
                "label": TELEGRAM_PR_READY_BUTTON_LABELS["details"],
                "callback_payload": {**callback_base, "action": "details"},
            },
            {
                "action": "open_pr",
                "label": TELEGRAM_PR_READY_BUTTON_LABELS["open_pr"],
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
        return _localize_pr_ready_card_payload(
            build_pr_ready_card_payload(
                repo=REPO,
                pr_number=pr_number,
                head_sha=head_sha,
                changed_files=changed_files,
                test_summary=TELEGRAM_CARD_TEST_SUMMARY,
                risk_summary=TELEGRAM_CARD_RISK_SUMMARY,
                pr_url=pr_url,
            ),
            pr_number,
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
    if not has_runner_task_body(issue.get("body") or ""):
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
            issue_branch(issue_number),
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
        issue_branch(issue_number),
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
                issue_branch(issue_number),
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


def finalize_local_worktree_success(
    workdir: str, codex_output: str, runner_task: RunnerTask
) -> str:
    files = changed_files(workdir)
    cleanup_runtime_artifacts(workdir)
    file_summary = (
        "Local worktree changed files:\n"
        + "\n".join(f"- {file_name}" for file_name in files)
        if files
        else "Local worktree changed files: none"
    )
    return (
        "DONE: Codex completed successfully in the local target-project "
        "worktree.\n\n"
        f"Selected Project: {runner_task.target_project}\n"
        f"Selected Repository: {runner_task.target_repository}\n"
        f"Issue worktree: `{workdir}`\n"
        "Target-repo output: not created.\n\n"
        f"{file_summary}\n\n"
        f"Codex output:\n```\n{codex_output.strip()}\n```"
    )


def block_issue(
    issue_number: int,
    message: str,
    remove_label: str = LABEL_READY,
    runner_task: RunnerTask | None = None,
) -> None:
    post_issue_comment(
        issue_number,
        report_runner_lane(f"BLOCKED: {message}", runner_task),
    )
    set_issue_label(issue_number, remove_label, LABEL_BLOCKED)
    notify_task_finished(issue_number, "BLOCKED")


def _maintenance_report(
    status: str, task_id: str, status_lines: list[str], success_criteria: str
) -> str:
    heading = (
        "DONE: Runner host maintenance task completed."
        if status == "DONE"
        else "BLOCKED: Runner host maintenance task did not complete."
    )
    return "\n".join(
        (
            heading,
            f"maintenance_task_id={task_id}",
            *status_lines,
            f"success_criteria={success_criteria}",
        )
    )


def _non_interactive_sudo(*args: str) -> list[str]:
    return ["sudo", "-n", *args]


def _run_maintenance_command(
    task_id: str,
    step: str,
    command: list[str],
    status_lines: list[str],
    cwd: str | Path | None = None,
) -> str | None:
    code, _output = run_command(command, cwd=cwd)
    if code == 0:
        status_lines.append(f"step={step} status=done")
        return None
    return _maintenance_report(
        "BLOCKED",
        task_id,
        [*status_lines, f"step={step} status=failed exit_code={code}"],
        "not_met",
    )


def _verify_maintenance_command_output(
    task_id: str,
    step: str,
    command: list[str],
    expected_output: str,
    status_lines: list[str],
) -> str | None:
    code, output = run_command(command)
    if code == 0 and output.strip() == expected_output:
        status_lines.append(f"step={step} status=done")
        return None
    return _maintenance_report(
        "BLOCKED",
        task_id,
        [*status_lines, f"step={step} status=failed"],
        "not_met",
    )


def sync_telegram_callback_poller_runtime(workdir: str) -> str:
    task_id = SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME
    status_lines: list[str] = []
    steps = (
        (
            "stop_callback_timer",
            _non_interactive_sudo("systemctl", "stop", TELEGRAM_CALLBACK_POLLER_TIMER),
            None,
        ),
        (
            "stop_callback_service",
            _non_interactive_sudo(
                "systemctl", "stop", TELEGRAM_CALLBACK_POLLER_SERVICE
            ),
            None,
        ),
        ("fetch_origin_main", ["git", "fetch", "origin", "main"], workdir),
        ("checkout_main", ["git", "checkout", "main"], workdir),
        ("pull_origin_main", ["git", "pull", "--ff-only", "origin", "main"], workdir),
    )
    for step, command, cwd in steps:
        report = _run_maintenance_command(task_id, step, command, status_lines, cwd)
        if report is not None:
            return report

    repository = Path(workdir)
    missing_files = [
        relative_path
        for relative_path in TELEGRAM_CALLBACK_POLLER_RUNTIME_FILES
        if not (repository / relative_path).is_file()
    ]
    if missing_files:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [
                *status_lines,
                "step=verify_callback_runtime_files status=failed",
                *[f"missing_file={file_name}" for file_name in missing_files],
            ],
            "not_met",
        )
    status_lines.append("step=verify_callback_runtime_files status=done")

    source_service = repository / "scripts" / TELEGRAM_CALLBACK_POLLER_SERVICE
    source_timer = repository / "scripts" / TELEGRAM_CALLBACK_POLLER_TIMER
    installed_service = f"/etc/systemd/system/{TELEGRAM_CALLBACK_POLLER_SERVICE}"
    installed_timer = f"/etc/systemd/system/{TELEGRAM_CALLBACK_POLLER_TIMER}"
    copy_and_start_steps = (
        (
            "copy_callback_service_unit",
            _non_interactive_sudo("cp", str(source_service), installed_service),
        ),
        (
            "copy_callback_timer_unit",
            _non_interactive_sudo("cp", str(source_timer), installed_timer),
        ),
        (
            "own_callback_service_unit",
            _non_interactive_sudo("chown", "root:root", installed_service),
        ),
        (
            "own_callback_timer_unit",
            _non_interactive_sudo("chown", "root:root", installed_timer),
        ),
        (
            "mode_callback_service_unit",
            _non_interactive_sudo("chmod", "0644", installed_service),
        ),
        (
            "mode_callback_timer_unit",
            _non_interactive_sudo("chmod", "0644", installed_timer),
        ),
        ("systemd_daemon_reload", _non_interactive_sudo("systemctl", "daemon-reload")),
        (
            "enable_callback_timer",
            _non_interactive_sudo("systemctl", "enable", TELEGRAM_CALLBACK_POLLER_TIMER),
        ),
        (
            "start_callback_timer",
            _non_interactive_sudo("systemctl", "start", TELEGRAM_CALLBACK_POLLER_TIMER),
        ),
        (
            "run_callback_service_once",
            _non_interactive_sudo(
                "systemctl", "start", TELEGRAM_CALLBACK_POLLER_SERVICE
            ),
        ),
        (
            "verify_callback_timer_active",
            _non_interactive_sudo(
                "systemctl", "is-active", "--quiet", TELEGRAM_CALLBACK_POLLER_TIMER
            ),
        ),
    )
    for step, command in copy_and_start_steps:
        report = _run_maintenance_command(task_id, step, command, status_lines)
        if report is not None:
            return report

    report = _verify_maintenance_command_output(
        task_id,
        "verify_callback_service_result",
        _non_interactive_sudo(
            "systemctl",
            "show",
            "--property=Result",
            "--value",
            TELEGRAM_CALLBACK_POLLER_SERVICE,
        ),
        "success",
        status_lines,
    )
    if report is not None:
        return report

    return _maintenance_report("DONE", task_id, status_lines, "met")


_ENSURE_CALLBACK_HMAC_SCRIPT = """\
from pathlib import Path
import secrets
import sys

path = Path(sys.argv[1])
name = sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines()
prefix = f"{name}="
replacement = f"{prefix}{secrets.token_urlsafe(48)}"
for index, line in enumerate(lines):
    if line.startswith(prefix):
        if line[len(prefix):].strip():
            break
        lines[index] = replacement
        path.write_text("\\n".join(lines) + "\\n", encoding="utf-8")
        break
else:
    if lines:
        lines.append(replacement)
    else:
        lines = [replacement]
    path.write_text("\\n".join(lines) + "\\n", encoding="utf-8")
"""

_VERIFY_CALLBACK_HMAC_SCRIPT = """\
from pathlib import Path
import sys

prefix = f"{sys.argv[2]}="
lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
raise SystemExit(
    0 if any(line.startswith(prefix) and line[len(prefix):].strip() for line in lines) else 1
)
"""


def ensure_telegram_callback_local_config() -> str:
    task_id = ENSURE_TELEGRAM_CALLBACK_LOCAL_CONFIG
    status_lines: list[str] = []
    steps = (
        (
            "create_callback_local_config",
            _non_interactive_sudo("touch", TELEGRAM_CALLBACK_LOCAL_CONFIG),
        ),
        (
            "own_callback_local_config",
            _non_interactive_sudo(
                "chown", "root:root", TELEGRAM_CALLBACK_LOCAL_CONFIG
            ),
        ),
        (
            "mode_callback_local_config",
            _non_interactive_sudo("chmod", "0600", TELEGRAM_CALLBACK_LOCAL_CONFIG),
        ),
        (
            "ensure_callback_hmac_secret",
            _non_interactive_sudo(
                "python3",
                "-c",
                _ENSURE_CALLBACK_HMAC_SCRIPT,
                TELEGRAM_CALLBACK_LOCAL_CONFIG,
                TELEGRAM_CALLBACK_HMAC_ENV,
            ),
        ),
        (
            "verify_callback_hmac_secret",
            _non_interactive_sudo(
                "python3",
                "-c",
                _VERIFY_CALLBACK_HMAC_SCRIPT,
                TELEGRAM_CALLBACK_LOCAL_CONFIG,
                TELEGRAM_CALLBACK_HMAC_ENV,
            ),
        ),
    )
    for step, command in steps:
        report = _run_maintenance_command(task_id, step, command, status_lines)
        if report is not None:
            return report
    return _maintenance_report("DONE", task_id, status_lines, "met")


def _target_project_metadata_field(body: str) -> str | None:
    metadata = (body or "").split("```task", 1)[0]
    return _body_field(metadata, "Target Project")


def _project_checkout_path_is_under_runner_base(checkout_path: Path) -> bool:
    try:
        checkout_path.resolve(strict=False).relative_to(RUNNER_PROJECT_CHECKOUT_BASE)
    except ValueError:
        return False
    return True


def _remote_url_matches_project_repo(remote_url: str, repo: str) -> bool:
    remote_url = remote_url.strip()
    expected = repo.strip().removesuffix(".git")
    candidates = {
        expected,
        f"https://github.com/{expected}",
        f"git@github.com:{expected}",
        f"ssh://git@github.com/{expected}",
    }
    return remote_url.removesuffix(".git") in candidates


def _registered_project_checkout(
    task_id: str, body: str
) -> tuple[RegisteredProjectCheckout | None, str | None]:
    target_project = _target_project_metadata_field(body)
    if target_project is None:
        return None, _maintenance_report(
            "BLOCKED",
            task_id,
            ["reason=missing_target_project"],
            "not_met",
        )

    projects = load_runner_project_tree().get("projects")
    project = projects.get(target_project) if isinstance(projects, dict) else None
    if not isinstance(project, dict):
        return None, _maintenance_report(
            "BLOCKED",
            task_id,
            [
                f"target_project={target_project}",
                "reason=target_project_unknown",
            ],
            "not_met",
        )

    checkout_path_text = str(project["checkout_path"])
    checkout_path = Path(checkout_path_text)
    status_lines: list[str] = []
    status_lines.extend(
        (
            f"target_project={target_project}",
            f"target_repository={project['repo']}",
            f"checkout_path={checkout_path_text}",
        )
    )
    if any(part == ".." for part in checkout_path.parts):
        return None, _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=checkout_path_traversal"],
            "not_met",
        )
    if not _project_checkout_path_is_under_runner_base(checkout_path):
        return None, _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=checkout_path_unsafe"],
            "not_met",
        )
    return RegisteredProjectCheckout(
        target_project=target_project,
        repo=str(project["repo"]),
        checkout_path_text=checkout_path_text,
        checkout_path=checkout_path,
        status_lines=status_lines,
    ), None


def _verify_registered_project_checkout(
    task_id: str, registered_checkout: RegisteredProjectCheckout
) -> str:
    checkout_path = registered_checkout.checkout_path
    status_lines = registered_checkout.status_lines
    if not checkout_path.exists():
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=checkout_path_missing"],
            "not_met",
        )
    if not (checkout_path / ".git").exists():
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=checkout_git_missing"],
            "not_met",
        )

    command = ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]
    code, output = run_command(command)
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=read_origin_remote status=failed"],
            "not_met",
        )
    if not _remote_url_matches_project_repo(output, registered_checkout.repo):
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=verify_origin_remote status=failed"],
            "not_met",
        )

    return _maintenance_report(
        "DONE",
        task_id,
        [
            *status_lines,
            "step=read_origin_remote status=done",
            "step=verify_origin_remote status=done",
        ],
        "met",
    )


def check_project_checkout(body: str) -> str:
    task_id = CHECK_PROJECT_CHECKOUT
    registered_checkout, report = _registered_project_checkout(task_id, body)
    if report is not None:
        return report
    assert registered_checkout is not None
    return _verify_registered_project_checkout(task_id, registered_checkout)


def ensure_project_checkout(body: str) -> str:
    task_id = ENSURE_PROJECT_CHECKOUT
    registered_checkout, report = _registered_project_checkout(task_id, body)
    if report is not None:
        return report
    assert registered_checkout is not None

    checkout_path = registered_checkout.checkout_path
    status_lines = registered_checkout.status_lines
    if checkout_path.exists():
        return _verify_registered_project_checkout(task_id, registered_checkout)

    try:
        checkout_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [
                *status_lines,
                "step=prepare_checkout_parent status=failed",
                "reason=checkout_parent_prepare_failed",
            ],
            "not_met",
        )
    status_lines.append("step=prepare_checkout_parent status=done")

    clone_url = f"https://github.com/{registered_checkout.repo.removesuffix('.git')}.git"
    code, _output = run_command(["git", "clone", clone_url, str(checkout_path)])
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, f"step=prepare_checkout status=failed exit_code={code}"],
            "not_met",
        )
    status_lines.append("step=prepare_checkout status=done")
    return _verify_registered_project_checkout(task_id, registered_checkout)


def _pr_branch_validation_metadata(
    body: str,
) -> tuple[PrBranchValidationRequest | None, str | None]:
    metadata = (body or "").split("```task", 1)[0]
    pr_number = _body_field(metadata, "Pull Request")
    expected_head_sha = _body_field(metadata, "Expected Head SHA")
    profile = _body_field(metadata, "Validation Profile") or "full_pytest"
    if not isinstance(pr_number, str) or not re.fullmatch(r"[1-9]\d*", pr_number):
        return None, "missing_or_invalid_pull_request"
    if (
        expected_head_sha is not None
        and _HEAD_SHA_RE.fullmatch(expected_head_sha) is None
    ):
        return None, "invalid_expected_head_sha"
    if profile not in PR_BRANCH_VALIDATION_PROFILES:
        return None, "unsupported_validation_profile"
    return (
        PrBranchValidationRequest(
            pr_number=int(pr_number),
            expected_head_sha=(
                expected_head_sha.lower()
                if isinstance(expected_head_sha, str)
                else None
            ),
            profile=profile,
        ),
        None,
    )


def _get_pr_branch_validation_state(pr_number: int) -> dict[str, Any]:
    code, output = run_command(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            REPO,
            "--json",
            "number,state,baseRefName,headRefName,headRefOid",
        ]
    )
    if code != 0:
        raise RuntimeError("gh pr view failed")
    parsed = json.loads(output or "{}")
    if not isinstance(parsed, dict):
        raise RuntimeError("gh pr view returned non-object JSON")
    return parsed


def _validation_worktree_path(pr_number: int) -> Path:
    return worktree_root() / PR_BRANCH_VALIDATION_WORKTREE_DIR / f"pr-{pr_number}"


def _ensure_safe_validation_worktree_path(path: str | Path) -> Path:
    root = worktree_root().resolve(strict=False)
    candidate = Path(path).expanduser().resolve(strict=False)
    if candidate == root:
        raise ValueError("validation worktree cannot be the worktree root")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("validation worktree is outside runner worktree root") from exc
    return candidate


def _pr_branch_validation_block_reason(
    request: PrBranchValidationRequest, pr_state: dict[str, Any]
) -> str | None:
    if pr_state.get("number") != request.pr_number:
        return "pr_number_mismatch"
    if str(pr_state.get("state") or "").upper() != "OPEN":
        return "pr_not_open"
    if pr_state.get("baseRefName") != "main":
        return "pr_base_not_main"
    head_sha = str(pr_state.get("headRefOid") or "").lower()
    if _HEAD_SHA_RE.fullmatch(head_sha) is None:
        return "pr_head_sha_invalid"
    if request.expected_head_sha is not None and head_sha != request.expected_head_sha:
        return "expected_head_sha_mismatch"
    return None


def _sanitize_validation_command_output(output: str) -> str:
    sanitized = _ANSI_ESCAPE_RE.sub("", output or "")
    sanitized = sanitized.replace("\r\n", "\n").replace("\r", "\n")
    sanitized = "".join(
        character
        if character == "\n" or character == "\t" or 32 <= ord(character) < 127
        else "?"
        for character in sanitized
    )
    safe_lines: list[str] = []
    for line in sanitized.split("\n"):
        if _ENV_ASSIGNMENT_LINE_RE.fullmatch(line):
            safe_lines.append("[redacted environment variable]")
            continue
        safe_lines.append(
            _SENSITIVE_OUTPUT_VALUE_RE.sub(r"\1=[redacted]", line).rstrip()
        )
    return "\n".join(safe_lines).strip()


def _bounded_validation_command_output(output: str) -> str:
    sanitized = _sanitize_validation_command_output(output)
    if not sanitized:
        return "(no output)"
    if len(sanitized) <= VALIDATION_FAILED_OUTPUT_LIMIT:
        return sanitized
    marker = f"\n{VALIDATION_FAILED_OUTPUT_TRUNCATED_MARKER}"
    return sanitized[: VALIDATION_FAILED_OUTPUT_LIMIT - len(marker)].rstrip() + marker


def _validation_command_failure_lines(
    index: int, command: tuple[str, ...], exit_code: int, output: str
) -> list[str]:
    return [
        f"step=validation_profile_command_{index} status=failed exit_code={exit_code}",
        f"failed_command={shlex.join(command)}",
        "failed_output_start",
        _bounded_validation_command_output(output),
        "failed_output_end",
    ]


def validate_pr_branch(body: str) -> str:
    task_id = VALIDATE_PR_BRANCH
    request, reason = _pr_branch_validation_metadata(body)
    if reason is not None:
        return _maintenance_report("BLOCKED", task_id, [f"reason={reason}"], "not_met")
    assert request is not None

    status_lines = [
        f"repository={REPO}",
        f"pull_request={request.pr_number}",
        f"validation_profile={request.profile}",
    ]
    try:
        pr_state = _get_pr_branch_validation_state(request.pr_number)
    except Exception:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=read_pr_metadata status=failed"],
            "not_met",
        )
    status_lines.append("step=read_pr_metadata status=done")

    block_reason = _pr_branch_validation_block_reason(request, pr_state)
    if block_reason is not None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, f"reason={block_reason}"],
            "not_met",
        )

    head_sha = str(pr_state["headRefOid"]).lower()
    status_lines.append(f"head_sha={head_sha}")
    try:
        validation_path = _ensure_safe_validation_worktree_path(
            _validation_worktree_path(request.pr_number)
        )
    except ValueError:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=validation_worktree_path_unsafe"],
            "not_met",
        )

    try:
        validation_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=prepare_validation_parent status=failed"],
            "not_met",
        )
    status_lines.append("step=prepare_validation_parent status=done")

    if validation_path.exists():
        code, _output = run_command(
            ["git", "worktree", "remove", "--force", str(validation_path)], cwd=ROOT
        )
        if code != 0:
            return _maintenance_report(
                "BLOCKED",
                task_id,
                [
                    *status_lines,
                    (
                        "step=remove_validation_worktree status=failed "
                        f"exit_code={code}"
                    ),
                ],
                "not_met",
            )
        status_lines.append("step=remove_validation_worktree status=done")

    pr_ref = f"refs/remotes/origin/pr-validation/{request.pr_number}"
    fetch_refspec = f"+refs/pull/{request.pr_number}/head:{pr_ref}"
    code, _output = run_command(["git", "fetch", "origin", fetch_refspec], cwd=ROOT)
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, f"step=fetch_pr_head status=failed exit_code={code}"],
            "not_met",
        )
    status_lines.append("step=fetch_pr_head status=done")

    code, output = run_command(["git", "rev-parse", f"{pr_ref}^{{commit}}"], cwd=ROOT)
    if code != 0 or output.strip().lower() != head_sha:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=verify_fetched_head status=failed"],
            "not_met",
        )
    status_lines.append("step=verify_fetched_head status=done")

    code, _output = run_command(
        ["git", "worktree", "add", "--detach", str(validation_path), head_sha],
        cwd=ROOT,
    )
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [
                *status_lines,
                f"step=checkout_validation_head status=failed exit_code={code}",
            ],
            "not_met",
        )
    status_lines.append("step=checkout_validation_head status=done")

    code, output = run_command(["git", "rev-parse", "HEAD"], cwd=validation_path)
    if code != 0 or output.strip().lower() != head_sha:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=verify_validation_head status=failed"],
            "not_met",
        )
    status_lines.append("step=verify_validation_head status=done")

    for index, command in enumerate(PR_BRANCH_VALIDATION_PROFILES[request.profile], 1):
        code, output = run_command(list(command), cwd=validation_path)
        if code != 0:
            return _maintenance_report(
                "BLOCKED",
                task_id,
                [
                    *status_lines,
                    *_validation_command_failure_lines(
                        index, command, code, output
                    ),
                ],
                "not_met",
            )
        status_lines.append(f"step=validation_profile_command_{index} status=done")

    return _maintenance_report("DONE", task_id, status_lines, "met")


def _pr_mergeability_inspection_metadata(
    body: str,
) -> tuple[PrMergeabilityInspectionRequest | None, str | None]:
    metadata = (body or "").split("```task", 1)[0]
    repository = _body_field(metadata, "Repository")
    pr_number = _body_field(metadata, "Pull Request")
    expected_head_sha = _body_field(metadata, "Expected Head SHA")
    if repository is not None and repository != REPO:
        return None, "unsupported_repository"
    if not isinstance(pr_number, str) or not re.fullmatch(r"[1-9]\d*", pr_number):
        return None, "missing_or_invalid_pull_request"
    if (
        expected_head_sha is not None
        and _HEAD_SHA_RE.fullmatch(expected_head_sha) is None
    ):
        return None, "invalid_expected_head_sha"
    return (
        PrMergeabilityInspectionRequest(
            pr_number=int(pr_number),
            expected_head_sha=(
                expected_head_sha.lower()
                if isinstance(expected_head_sha, str)
                else None
            ),
        ),
        None,
    )


def _github_api_json(path: str, query: dict[str, str] | None = None) -> object:
    url = f"https://api.github.com{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "skeleton-runner-maintenance",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8") or "null")


def _github_api_list(path: str, key: str | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = _github_api_json(
            path, {"per_page": "100", "page": str(page)}
        )
        page_items = payload.get(key) if key and isinstance(payload, dict) else payload
        if not isinstance(page_items, list):
            raise RuntimeError("GitHub API list response was malformed")
        items.extend(item for item in page_items if isinstance(item, dict))
        if len(page_items) < 100:
            return items
        page += 1


def _get_pr_mergeability_state(pr_number: int) -> dict[str, Any]:
    pr_path = f"/repos/{REPO}/pulls/{pr_number}"
    pr = _github_api_json(pr_path)
    if not isinstance(pr, dict):
        raise RuntimeError("GitHub PR response was malformed")

    files = _github_api_list(f"{pr_path}/files")
    head_sha = str((pr.get("head") or {}).get("sha") or "")
    base_sha = str((pr.get("base") or {}).get("sha") or "")

    compare: dict[str, Any] = {}
    if _HEAD_SHA_RE.fullmatch(base_sha) and _HEAD_SHA_RE.fullmatch(head_sha):
        compare_payload = _github_api_json(
            f"/repos/{REPO}/compare/{base_sha}...{head_sha}"
        )
        if isinstance(compare_payload, dict):
            compare = compare_payload

    combined_status: dict[str, Any] = {}
    check_runs: list[dict[str, Any]] = []
    if _HEAD_SHA_RE.fullmatch(head_sha):
        status_payload = _github_api_json(f"/repos/{REPO}/commits/{head_sha}/status")
        if isinstance(status_payload, dict):
            combined_status = status_payload
        check_runs = _github_api_list(
            f"/repos/{REPO}/commits/{head_sha}/check-runs", key="check_runs"
        )

    return {
        "pr": pr,
        "files": files,
        "compare": compare,
        "combined_status": combined_status,
        "check_runs": check_runs,
    }


def _github_bool_value(value: object) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    return str(value)


def _validation_summary(
    combined_status: dict[str, Any], check_runs: list[dict[str, Any]]
) -> tuple[str, str]:
    statuses = combined_status.get("statuses")
    status_items = statuses if isinstance(statuses, list) else []
    status_state = str(combined_status.get("state") or "").lower()
    if not status_items and not check_runs:
        return "missing", "validation_missing"

    check_states = {
        str(check.get("status") or "").lower() for check in check_runs
    }
    check_conclusions = {
        str(check.get("conclusion") or "").lower()
        for check in check_runs
        if check.get("conclusion") is not None
    }
    checks_success = not check_runs or (
        check_states <= {"completed"}
        and check_conclusions <= {"success", "neutral", "skipped"}
    )
    statuses_success = not status_items or status_state == "success"
    if statuses_success and checks_success:
        return "success", "none"
    return "not_success", "validation_not_success"


def _pr_mergeability_next_action(
    pr: dict[str, Any],
    compare: dict[str, Any],
    validation_state: str,
    request: PrMergeabilityInspectionRequest,
) -> tuple[str, str, str]:
    state = str(pr.get("state") or "").upper()
    head_sha = str((pr.get("head") or {}).get("sha") or "").lower()
    mergeable = pr.get("mergeable")
    mergeable_state = str(pr.get("mergeable_state") or "").lower()
    behind_by = compare.get("behind_by")
    compare_status = str(compare.get("status") or "").lower()

    if state != "OPEN":
        return "BLOCKED", "pr_not_open", "obsolete_close_or_reopen_request"
    if request.expected_head_sha is not None and head_sha != request.expected_head_sha:
        return "BLOCKED", "expected_head_sha_mismatch", "refresh_inspection_request"
    if pr.get("draft") is True:
        return "BLOCKED", "pr_is_draft", "mark_pr_ready_for_review"
    if (
        isinstance(behind_by, int)
        and behind_by > 0
        or compare_status in {"behind", "diverged"}
        or mergeable_state == "behind"
    ):
        return "BLOCKED", "branch_behind_or_diverged", "refresh_pr_branch"
    if mergeable is False or mergeable_state in {"dirty", "blocked"}:
        return "BLOCKED", "pr_has_merge_conflicts", "resolve_merge_conflicts"
    if validation_state == "missing":
        return "BLOCKED", "validation_missing", "run_required_validation"
    if validation_state != "success":
        return "BLOCKED", "validation_not_success", "wait_for_or_fix_validation"
    if mergeable is True:
        return "DONE", "none", "mark_ready_or_merge"
    return "BLOCKED", "mergeability_unknown", "refresh_mergeability_inspection"


def inspect_pr_mergeability(body: str) -> str:
    task_id = INSPECT_PR_MERGEABILITY
    request, reason = _pr_mergeability_inspection_metadata(body)
    if reason is not None:
        return _maintenance_report("BLOCKED", task_id, [f"reason={reason}"], "not_met")
    assert request is not None

    status_lines = [f"repository={REPO}", f"pull_request={request.pr_number}"]
    try:
        state = _get_pr_mergeability_state(request.pr_number)
    except Exception:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=read_pr_metadata status=failed"],
            "not_met",
        )

    pr = state["pr"]
    files = state["files"]
    compare = state["compare"]
    combined_status = state["combined_status"]
    check_runs = state["check_runs"]

    base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
    head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
    base_repo = base.get("repo") if isinstance(base.get("repo"), dict) else {}
    if base_repo.get("full_name") != REPO:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=unsupported_repository"],
            "not_met",
        )

    validation_state, validation_reason = _validation_summary(
        combined_status, check_runs
    )
    report_status, reason, next_action = _pr_mergeability_next_action(
        pr, compare, validation_state, request
    )
    changed_files = [
        str(file.get("filename"))
        for file in files
        if isinstance(file.get("filename"), str)
    ]
    mergeable = _github_bool_value(pr.get("mergeable"))
    mergeable_state = str(pr.get("mergeable_state") or "unknown")
    status_lines.extend(
        (
            "step=read_pr_metadata status=done",
            f"pr_state={str(pr.get('state') or '').lower()}",
            f"draft={_github_bool_value(pr.get('draft'))}",
            f"base_branch={base.get('ref')}",
            f"base_sha={base.get('sha')}",
            f"head_branch={head.get('ref')}",
            f"head_sha={head.get('sha')}",
            f"mergeable={mergeable}",
            f"mergeable_state={mergeable_state}",
            f"changed_file_count={len(changed_files)}",
            f"changed_files={','.join(changed_files) if changed_files else '(none)'}",
            f"compare_status={compare.get('status', 'unknown')}",
            f"ahead_by={compare.get('ahead_by', 'unknown')}",
            f"behind_by={compare.get('behind_by', 'unknown')}",
            f"validation_state={validation_state}",
            f"reason={reason if reason != 'none' else validation_reason}",
            f"next_action={next_action}",
        )
    )
    return _maintenance_report(
        report_status,
        task_id,
        status_lines,
        "met" if report_status == "DONE" else "not_met",
    )


def dispatch_runtime_maintenance_task(
    task_id: str, workdir: str, body: str = ""
) -> str:
    if task_id not in RUNTIME_MAINTENANCE_TASK_IDS:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            ["reason=maintenance_task_id_not_allowlisted"],
            "not_met",
        )
    try:
        if task_id == SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME:
            return sync_telegram_callback_poller_runtime(workdir)
        if task_id == ENSURE_TELEGRAM_CALLBACK_LOCAL_CONFIG:
            return ensure_telegram_callback_local_config()
        if task_id == ENSURE_PROJECT_CHECKOUT:
            return ensure_project_checkout(body)
        if task_id == VALIDATE_PR_BRANCH:
            return validate_pr_branch(body)
        if task_id == INSPECT_PR_MERGEABILITY:
            return inspect_pr_mergeability(body)
        return check_project_checkout(body)
    except Exception:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            ["reason=maintenance_step_raised"],
            "not_met",
        )


def maintenance_report_is_done(report: str) -> bool:
    return (
        report.startswith("DONE:")
        and re.search(r"\bBLOCKED\b", report) is None
        and re.search(r"^success_criteria=not_met\s*$", report, re.MULTILINE) is None
    )


def get_pr_merge_state(pr_number: int) -> dict[str, Any]:
    code, output = run_command(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            REPO,
            "--json",
            "number,state,isDraft,mergeable,headRefOid,comments",
        ]
    )
    if code != 0:
        raise RuntimeError(f"gh pr view failed:\n{output}")
    parsed = json.loads(output or "{}")
    if not isinstance(parsed, dict):
        raise RuntimeError("gh pr view returned non-object JSON")
    return parsed


def telegram_approve_audit_matches_request(
    pr_state: dict[str, Any],
    request: TelegramApprovedPrMergeRequest,
) -> bool:
    comments = pr_state.get("comments")
    if not isinstance(comments, list):
        return False

    expected_lines = (
        "Operator event record (Telegram callback stage 1)",
        f"Pull request: #{request.pr_number}",
        "Action: telegram_approve",
        f"Head marker: {request.approved_head_sha[:8]}",
        f"Callback digest: {request.callback_digest}",
        "Result: recorded",
        "Verified approval record: signed_telegram_callback",
        f"Verified head SHA: {request.approved_head_sha}",
    )
    for comment in comments:
        body = comment.get("body") if isinstance(comment, dict) else None
        if isinstance(body, str) and all(line in body.splitlines() for line in expected_lines):
            return True
    return False


def telegram_approve_digest_is_signed(
    request: TelegramApprovedPrMergeRequest,
) -> bool:
    hmac_secret = os.environ.get(TELEGRAM_CALLBACK_HMAC_ENV)
    if not hmac_secret:
        return False
    digest = hmac.new(
        hmac_secret.encode("utf-8"),
        (
            f"tpr1:approve:p{request.pr_number}:"
            f"{request.approved_head_sha[:8]}"
        ).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()[:12]
    return hmac.compare_digest(request.callback_digest, digest)


def _pr_merge_block_reason(
    request: TelegramApprovedPrMergeRequest,
    pr_state: dict[str, Any],
) -> str | None:
    if not telegram_approve_digest_is_signed(request):
        return "Telegram approve callback HMAC signature is invalid."
    if pr_state.get("number") != request.pr_number:
        return "GitHub PR number does not match the approved merge request."
    if str(pr_state.get("state") or "").upper() != "OPEN":
        return "Approved PR is not open."
    if pr_state.get("isDraft") is not False:
        return "Approved PR is still draft."
    if str(pr_state.get("mergeable") or "").upper() != "MERGEABLE":
        return "Approved PR is not mergeable."
    if str(pr_state.get("headRefOid") or "").lower() != request.approved_head_sha:
        return "Approved PR head does not match the Telegram button head."
    if not telegram_approve_audit_matches_request(pr_state, request):
        return "Signed Telegram approve audit does not match this merge request."
    if request.action != TELEGRAM_APPROVED_PR_MERGE_ACTION:
        return "Approved PR merge action is not allowlisted."
    return None


def execute_telegram_approved_pr_merge(
    request: TelegramApprovedPrMergeRequest,
) -> str:
    pr_state = get_pr_merge_state(request.pr_number)
    block_reason = _pr_merge_block_reason(request, pr_state)
    if block_reason is not None:
        return f"BLOCKED: {block_reason}"

    command = [
        "gh",
        "pr",
        "merge",
        str(request.pr_number),
        "--repo",
        REPO,
        "--squash",
        "--match-head-commit",
        request.approved_head_sha,
    ]
    code, _output = run_command(command)
    if code != 0:
        return "BLOCKED: GitHub squash merge failed."
    return (
        f"DONE: Squash merged approved PR #{request.pr_number}.\n"
        f"approved_head_sha={request.approved_head_sha}\n"
        f"merge_action={request.action}"
    )


def process_telegram_approved_pr_merge_issue(
    issue_number: int,
    request: TelegramApprovedPrMergeRequest,
) -> None:
    report = execute_telegram_approved_pr_merge(request)
    post_issue_comment(issue_number, report)
    status = "DONE" if report.startswith("DONE:") else "BLOCKED"
    set_issue_label(
        issue_number,
        LABEL_RUNNING,
        LABEL_DONE if status == "DONE" else LABEL_BLOCKED,
    )
    notify_task_finished(issue_number, status, report)


def process_runtime_maintenance_issue(
    issue_number: int, task_id: str, workdir: str, body: str = ""
) -> None:
    report = dispatch_runtime_maintenance_task(task_id, workdir, body)
    post_issue_comment(issue_number, report)
    if maintenance_report_is_done(report):
        set_issue_label(issue_number, LABEL_RUNNING, LABEL_DONE)
        notify_task_finished(issue_number, "DONE", report)
        return
    set_issue_label(issue_number, LABEL_RUNNING, LABEL_BLOCKED)
    notify_task_finished(issue_number, "BLOCKED", report)


def process_issue(issue: dict[str, Any], workdir: str | None = None) -> None:
    issue_number = int(issue["number"])
    if not is_open_task_issue(issue):
        return

    coordinator_workdir = str(Path(workdir) if workdir is not None else DEFAULT_WORKDIR)
    issue_workdir: str | None = None
    claimed = False
    runner_task: RunnerTask | None = None
    try:
        issue_body = issue.get("body") or ""
        maintenance_mode, maintenance_task_id = extract_runtime_maintenance_task_id(
            issue_body
        )
        merge_mode, merge_request, merge_reason = (
            extract_telegram_approved_pr_merge_request(issue_body)
        )
        if maintenance_mode and maintenance_task_id is None:
            block_issue(
                issue_number,
                "Runtime maintenance task id is missing. Use `Maintenance Task ID:`.",
            )
            return
        if maintenance_mode and maintenance_task_id not in RUNTIME_MAINTENANCE_TASK_IDS:
            block_issue(
                issue_number,
                f"Runtime maintenance task id `{maintenance_task_id}` is not allowlisted.",
            )
            return
        if merge_mode and merge_request is None:
            block_issue(
                issue_number,
                merge_reason or "Telegram approved merge request is malformed.",
            )
            return

        if maintenance_mode:
            task_content = ""
        else:
            runner_task, task_reason = extract_runner_task(issue_body)
            if runner_task is None:
                if task_reason is not None:
                    block_issue(issue_number, task_reason)
                    return
                if merge_mode:
                    task_content = ""
                else:
                    block_issue(
                        issue_number,
                        "No fenced task block found. Add a fence that starts with ```task.",
                    )
                    return
            else:
                task_content = runner_task.content

        if runner_task is not None:
            execution_block_reason = project_execution_block_reason(runner_task)
            if execution_block_reason is not None:
                block_issue(
                    issue_number,
                    execution_block_reason,
                    runner_task=runner_task,
                )
                return

        apply_runner_lane_label(issue_number, runner_task)

        if maintenance_mode:
            clean, status_output = ensure_clean_worktree(coordinator_workdir)
            if not clean:
                block_issue(
                    issue_number,
                    "Runner worktree is not clean before starting.\n\n"
                    f"git status --short:\n```\n{status_output.strip()}\n```",
                )
                return

        set_issue_label(issue_number, LABEL_READY, LABEL_RUNNING)
        claimed = True

        if maintenance_mode and maintenance_task_id is not None:
            process_runtime_maintenance_issue(
                issue_number, maintenance_task_id, coordinator_workdir, issue_body
            )
            return
        if merge_mode and merge_request is not None:
            process_telegram_approved_pr_merge_issue(issue_number, merge_request)
            return

        target_repository = (
            runner_task.target_repository if runner_task is not None else QUEUE_REPOSITORY
        )
        local_target_worktree = target_repository != QUEUE_REPOSITORY
        if local_target_worktree:
            worktree_code, worktree_output, worktree_path = (
                prepare_target_repository_issue_worktree(
                    target_repository,
                    issue_number,
                )
            )
        else:
            worktree_code, worktree_output, worktree_path = prepare_issue_branch(
                issue_number, coordinator_workdir
            )
        if worktree_code != 0:
            block_issue(
                issue_number,
                "Issue worktree preparation failed:\n"
                f"```\n{worktree_output}\n```"
                + issue_workspace_review_note(worktree_path),
                remove_label=LABEL_RUNNING,
                runner_task=runner_task,
            )
            return
        issue_workdir = str(worktree_path)

        cleanup_runtime_artifacts(issue_workdir)
        codex_code, codex_output = run_codex_task(
            task_content, issue_workdir, runner_task
        )
        cleanup_runtime_artifacts(issue_workdir)
        if codex_code != 0:
            block_issue(
                issue_number,
                f"Codex task failed:\n```\n{codex_output}\n```"
                + issue_workspace_review_note(issue_workdir),
                remove_label=LABEL_RUNNING,
                runner_task=runner_task,
            )
            return
        marker = blocked_output_marker(codex_output)
        if marker is not None:
            report = report_runner_lane(
                blocked_codex_output_report(codex_output, marker, issue_workdir),
                runner_task,
            )
            post_issue_comment(issue_number, report)
            set_issue_label(issue_number, LABEL_RUNNING, LABEL_BLOCKED)
            notify_task_finished(issue_number, "BLOCKED", report)
            return

        if local_target_worktree and runner_task is not None:
            finalized_report = finalize_local_worktree_success(
                issue_workdir, codex_output, runner_task
            )
        else:
            finalized_report = finalize_success(issue, issue_workdir, codex_output)
        report = report_runner_lane(finalized_report, runner_task)
        cleanup_runtime_artifacts(issue_workdir)
        if local_target_worktree:
            cleanup_code, cleanup_output = cleanup_target_repository_issue_worktree(
                target_repository,
                issue_number,
            )
        else:
            cleanup_code, cleanup_output = cleanup_issue_worktree(
                issue_number, coordinator_workdir
            )
        if cleanup_code != 0:
            raise RuntimeError(
                "Issue workspace cleanup failed:\n"
                f"{cleanup_output.strip() or f'exit code {cleanup_code}'}"
            )
        status = runner_report_status(report)
        if status == "BLOCKED":
            report = blocked_final_report(report)
        post_issue_comment(issue_number, report)
        set_issue_label(
            issue_number,
            LABEL_RUNNING,
            LABEL_DONE if status == "DONE" else LABEL_BLOCKED,
        )
        notify_task_finished(issue_number, status, report)
    except Exception as exc:
        if issue_workdir is not None:
            cleanup_runtime_artifacts(issue_workdir)
        try:
            remove_label = LABEL_RUNNING if claimed else LABEL_READY
            block_issue(
                issue_number,
                f"Runner error:\n```\n{exc}\n```"
                + (
                    issue_workspace_review_note(issue_workdir)
                    if issue_workdir is not None
                    else ""
                ),
                remove_label=remove_label,
                runner_task=runner_task,
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
