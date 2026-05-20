from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


ALLOWED_VALIDATION_COMMANDS = frozenset(
    {
        "python3 -m pytest -q",
        "python -m pytest -q",
        "pytest -q",
        "git diff --check",
        "git status --short",
    }
)


@dataclass(frozen=True)
class RunnerBridgeRequest:
    repo: str
    base_ref: str
    task_title: str
    task_body: str
    allowed_files: tuple[str, ...]
    protected_files: tuple[str, ...]
    validation_commands: tuple[str, ...]
    approval_marker: Optional[str]


@dataclass(frozen=True)
class RunnerBridgeResult:
    status: str
    issue_number: Optional[int]
    dry_run_summary: str
    blocked_reason: Optional[str] = None


def dry_run_runner_bridge(request: RunnerBridgeRequest) -> RunnerBridgeResult:
    blocked_reason = _blocked_reason(request)
    if blocked_reason is not None:
        return RunnerBridgeResult(
            status="blocked",
            issue_number=None,
            dry_run_summary="No GitHub issue would be created.",
            blocked_reason=blocked_reason,
        )

    issue_body = render_issue_body(request)
    return RunnerBridgeResult(
        status="dry_run",
        issue_number=None,
        dry_run_summary=issue_body,
        blocked_reason=None,
    )


def render_issue_body(request: RunnerBridgeRequest) -> str:
    allowed_files = _render_list(request.allowed_files)
    protected_files = _render_list(request.protected_files)
    validation_commands = _render_list(request.validation_commands)

    return "\n".join(
        (
            f"Repository: {request.repo}",
            f"Base ref: {request.base_ref}",
            f"Task title: {request.task_title}",
            "",
            "Allowed files:",
            allowed_files,
            "",
            "Protected files:",
            protected_files,
            "",
            "Validation commands:",
            validation_commands,
            "",
            "Approval marker:",
            request.approval_marker.strip() if request.approval_marker is not None else "",
            "",
            "```task",
            request.task_body.strip(),
            "```",
        )
    )


def _blocked_reason(request: RunnerBridgeRequest) -> Optional[str]:
    if request.approval_marker is None or request.approval_marker.strip() == "":
        return "approval_marker is required for runner_bridge dry-run."

    overlap = sorted(set(request.allowed_files).intersection(request.protected_files))
    if overlap:
        return "protected_files overlap allowed_files: " + ", ".join(overlap)

    for command in request.validation_commands:
        if command not in ALLOWED_VALIDATION_COMMANDS:
            return f"validation command is not allowlisted: {command}"

    return None


def _render_list(items: tuple[str, ...]) -> str:
    if not items:
        return "- none"
    return "\n".join(f"- {item}" for item in items)
