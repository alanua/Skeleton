from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import hmac
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
RUNTIME_MAINTENANCE_TASK_IDS = frozenset(
    (SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME, ENSURE_TELEGRAM_CALLBACK_LOCAL_CONFIG)
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


@dataclass(frozen=True)
class RunnerLane:
    name: str


@dataclass(frozen=True)
class TargetRepository:
    repo: str
    worktree_root: Path
    coordinator_workdir: Path | None = None
    base_branch: str = "main"
    branch_prefix: str = "runner"


DEFAULT_RUNNER_LANE = RunnerLane("default")
ALLOWED_RUNNER_LANES = frozenset(RUNNER_LANE_LABELS)
DEFAULT_TARGET_REPOSITORY = TargetRepository(
    repo=REPO,
    worktree_root=DEFAULT_WORKTREE_ROOT,
)
ALLOWED_TARGET_REPOSITORIES = {
    REPO: DEFAULT_TARGET_REPOSITORY,
    "alanua/jeeves": TargetRepository(
        repo="alanua/jeeves",
        worktree_root=Path("/home/agent/agent-dev/worktrees/jeeves"),
        coordinator_workdir=Path("/home/agent/agent-dev/jeeves"),
    ),
}


@dataclass(frozen=True)
class RunnerTask:
    content: str
    lane: RunnerLane = DEFAULT_RUNNER_LANE
    target_repository: TargetRepository = DEFAULT_TARGET_REPOSITORY
    has_lane_metadata: bool = False
    has_target_repository_metadata: bool = False


@dataclass(frozen=True)
class TelegramApprovedPrMergeRequest:
    pr_number: int
    approved_head_sha: str
    callback_digest: str
    action: str = TELEGRAM_APPROVED_PR_MERGE_ACTION


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


def _repo_env_token(repo: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", repo).strip("_").upper()


def worktree_root(target_repository: TargetRepository | None = None) -> Path:
    target = target_repository or DEFAULT_TARGET_REPOSITORY
    legacy_root = os.environ.get("SKELETON_WORKTREE_ROOT")
    if target.repo == REPO and legacy_root:
        return Path(legacy_root).expanduser()
    configured_root = os.environ.get(
        f"SKELETON_WORKTREE_ROOT_{_repo_env_token(target.repo)}"
    )
    if configured_root:
        return Path(configured_root).expanduser()
    return target.worktree_root


def coordinator_workdir_for_target(
    target_repository: TargetRepository, default_workdir: str | Path
) -> Path:
    configured_workdir = os.environ.get(
        f"SKELETON_COORDINATOR_WORKDIR_{_repo_env_token(target_repository.repo)}"
    )
    if configured_workdir:
        return Path(configured_workdir).expanduser()
    if target_repository.coordinator_workdir is not None:
        return target_repository.coordinator_workdir
    return Path(default_workdir)


def issue_worktree_path(
    issue_number: int, target_repository: TargetRepository | None = None
) -> Path:
    return worktree_root(target_repository) / f"issue-{issue_number}"


def ensure_safe_worktree_path(
    path: str | Path, target_repository: TargetRepository | None = None
) -> Path:
    root = worktree_root(target_repository).resolve()
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


def issue_branch(
    issue_number: int, target_repository: TargetRepository | None = None
) -> str:
    target = target_repository or DEFAULT_TARGET_REPOSITORY
    return f"{target.branch_prefix}/issue-{issue_number}"


def format_command_output(command: list[str], output: str) -> str:
    return f"$ {' '.join(command)}\n{output}"


def prepare_issue_worktree(
    issue_number: int,
    coordinator_workdir: str | Path,
    target_repository: TargetRepository | None = None,
) -> tuple[int, str, Path]:
    target = target_repository or DEFAULT_TARGET_REPOSITORY
    path = ensure_safe_worktree_path(issue_worktree_path(issue_number, target), target)
    branch = issue_branch(issue_number, target)
    base_ref = f"origin/{target.base_branch}"
    target_coordinator_workdir = coordinator_workdir_for_target(target, coordinator_workdir)
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
        ["git", "worktree", "add", "-B", branch, str(path), base_ref],
    ):
        code, output = run_command(command, cwd=target_coordinator_workdir)
        outputs.append(format_command_output(command, output))
        if code != 0:
            return code, "\n".join(outputs), path
    return 0, "\n".join(outputs), path


def prepare_issue_branch(
    issue_number: int,
    coordinator_workdir: str | Path,
    target_repository: TargetRepository | None = None,
) -> tuple[int, str, Path]:
    return prepare_issue_worktree(issue_number, coordinator_workdir, target_repository)


def cleanup_issue_worktree(
    issue_number: int,
    coordinator_workdir: str | Path,
    target_repository: TargetRepository | None = None,
) -> tuple[int, str]:
    target = target_repository or DEFAULT_TARGET_REPOSITORY
    try:
        path = ensure_safe_worktree_path(issue_worktree_path(issue_number, target), target)
    except ValueError as exc:
        return 1, str(exc)
    if not path.exists():
        return 0, ""
    cleanup_runtime_artifacts(path)
    target_coordinator_workdir = coordinator_workdir_for_target(target, coordinator_workdir)
    outputs: list[str] = []
    for command in (
        ["git", "worktree", "remove", "--force", str(path)],
        ["git", "worktree", "prune"],
    ):
        code, output = run_command(command, cwd=target_coordinator_workdir)
        outputs.append(format_command_output(command, output))
        if code != 0:
            return code, "\n".join(outputs)
    return 0, "\n".join(outputs)


def issue_workspace_review_note(path: str | Path) -> str:
    return f"\n\nIssue workspace kept for review:\n`{path}`"


def report_runner_lane(report: str, task: RunnerTask | None) -> str:
    if task is None or not task.has_lane_metadata:
        return report
    heading, *details = report.splitlines()
    return "\n".join((heading, f"Runner Lane: {task.lane.name}", *details))


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


def extract_target_repository(
    body: str,
) -> tuple[TargetRepository | None, str | None]:
    metadata = (body or "").split("```task", 1)[0]
    repo_name = _body_field(metadata, "Target Repository") or _body_field(
        metadata, "Target Repo"
    )
    if repo_name is None:
        return DEFAULT_TARGET_REPOSITORY, None
    target = ALLOWED_TARGET_REPOSITORIES.get(repo_name)
    if target is None:
        allowed = ", ".join(f"`{name}`" for name in sorted(ALLOWED_TARGET_REPOSITORIES))
        return (
            None,
            f"Target repository `{repo_name}` is not allowlisted. Use {allowed}.",
        )
    return target, None


def extract_runner_task(body: str) -> tuple[RunnerTask | None, str | None]:
    content = extract_task_block(body)
    if content is None:
        return None, None
    metadata = (body or "").split("```task", 1)[0]
    lane, lane_reason = extract_runner_lane(body)
    if lane is None:
        return None, lane_reason
    target_repository, target_reason = extract_target_repository(body)
    if target_repository is None:
        return None, target_reason
    return RunnerTask(
        content=content,
        lane=lane,
        target_repository=target_repository,
        has_lane_metadata=(
            _body_field(metadata, "Runner Lane") is not None
            or _body_field(metadata, "Lane") is not None
        ),
        has_target_repository_metadata=(
            _body_field(metadata, "Target Repository") is not None
            or _body_field(metadata, "Target Repo") is not None
        ),
    ), None


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


def build_codex_task_prompt(task_content: str, workdir: str) -> str:
    return (
        "Runner assigned this task to the issue worktree at:\n"
        f"{workdir}\n\n"
        "Edit files only inside that issue worktree. Do not create or use a separate "
        "clone, checkout, or worktree for task output.\n\n"
        f"{task_content}"
    )


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
                build_codex_task_prompt(task_content, workdir),
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


def extract_report_repository(report: str) -> str:
    match = re.search(
        r"^Target Repository:\s*(?P<repo>\S+)\s*$",
        report,
        re.MULTILINE,
    )
    return match.group("repo") if match else REPO


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

    repo = extract_report_repository(report)
    if repo != REPO:
        return _build_details_only_card_payload(pr_url, pr_number)

    try:
        # Runner reports the commit pushed immediately before its draft PR URL;
        # that commit is the reviewed head for this DONE notification.
        return _localize_pr_ready_card_payload(
            build_pr_ready_card_payload(
                repo=repo,
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


def finalize_success(
    issue: dict[str, Any],
    workdir: str,
    codex_output: str,
    target_repository: TargetRepository | None = None,
) -> str:
    issue_number = int(issue["number"])
    target = target_repository or DEFAULT_TARGET_REPOSITORY
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
            issue_branch(issue_number, target),
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
        target.repo,
        "--base",
        target.base_branch,
        "--head",
        issue_branch(issue_number, target),
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
                issue_branch(issue_number, target),
                "--repo",
                target.repo,
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
        f"Target Repository: {target.repo}\n\n"
        "Changed files:\n"
        + "\n".join(f"- {file_name}" for file_name in files)
        + "\n\n"
        f"Pytest output:\n```\n{pytest_output.strip()}\n```\n\n"
        f"Commit: {commit_sha}\n"
        f"Draft PR: {pr_url}"
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


def dispatch_runtime_maintenance_task(task_id: str, workdir: str) -> str:
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
        return ensure_telegram_callback_local_config()
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
    issue_number: int, task_id: str, workdir: str
) -> None:
    report = dispatch_runtime_maintenance_task(task_id, workdir)
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

        runner_task, task_reason = extract_runner_task(issue_body)
        if runner_task is None:
            if task_reason is not None:
                block_issue(issue_number, task_reason)
                return
            if maintenance_mode or merge_mode:
                task_content = ""
            else:
                block_issue(
                    issue_number,
                    "No fenced task block found. Add a fence that starts with ```task.",
                )
                return
        else:
            task_content = runner_task.content

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
                issue_number, maintenance_task_id, coordinator_workdir
            )
            return
        if merge_mode and merge_request is not None:
            process_telegram_approved_pr_merge_issue(issue_number, merge_request)
            return

        worktree_code, worktree_output, worktree_path = prepare_issue_branch(
            issue_number, coordinator_workdir, runner_task.target_repository
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
        codex_code, codex_output = run_codex_task(task_content, issue_workdir)
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

        report = report_runner_lane(
            finalize_success(
                issue,
                issue_workdir,
                codex_output,
                runner_task.target_repository,
            ),
            runner_task,
        )
        cleanup_runtime_artifacts(issue_workdir)
        cleanup_code, cleanup_output = cleanup_issue_worktree(
            issue_number, coordinator_workdir, runner_task.target_repository
        )
        if cleanup_code != 0:
            raise RuntimeError(
                "Issue workspace cleanup failed:\n"
                f"{cleanup_output.strip() or f'exit code {cleanup_code}'}"
            )
        post_issue_comment(issue_number, report)
        set_issue_label(issue_number, LABEL_RUNNING, LABEL_DONE)
        notify_task_finished(issue_number, "DONE", report)
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
