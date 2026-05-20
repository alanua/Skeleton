from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import PurePosixPath


_BLOCKED = "blocked"
_DRY_RUN = "dry_run"
_SHELL_META_PATTERN = re.compile(r"[;&|<>`$\\\n\r]")
_ALLOWED_VALIDATION_TOOLS = frozenset({"python", "python3", "pytest", "git"})


@dataclass(frozen=True)
class RunnerBridgeRequest:
    repo: str
    base_ref: str
    task_title: str
    task_body: str
    allowed_files: tuple[str, ...]
    protected_files: tuple[str, ...]
    validation_commands: tuple[str, ...]
    approval_marker: str


@dataclass(frozen=True)
class RunnerBridgeResult:
    status: str
    dry_run_summary: str
    issue_number: int | None = None
    blocked_reason: str | None = None


def dry_run_runner_bridge(request: RunnerBridgeRequest) -> RunnerBridgeResult:
    blocked_reason = validate_request(request)
    if blocked_reason is not None:
        return RunnerBridgeResult(
            status=_BLOCKED,
            dry_run_summary="Runner bridge dry-run blocked before issue rendering.",
            blocked_reason=blocked_reason,
        )

    return RunnerBridgeResult(
        status=_DRY_RUN,
        dry_run_summary=render_github_issue_body(request),
    )


def validate_request(request: RunnerBridgeRequest) -> str | None:
    if not request.approval_marker.strip():
        return "approval_marker is required for runner_bridge dry-run handoff."

    allowed_files = {_normalize_relative_path(path) for path in request.allowed_files}
    protected_files = {_normalize_relative_path(path) for path in request.protected_files}

    if None in allowed_files:
        return "allowed_files must contain only relative repository paths."
    if None in protected_files:
        return "protected_files must contain only relative repository paths."

    overlap = sorted(allowed_files.intersection(protected_files))
    if overlap:
        return f"protected files overlap allowed_files: {', '.join(overlap)}"

    if not request.validation_commands:
        return "validation_commands must include at least one deterministic command."

    for command in request.validation_commands:
        if not _is_valid_validation_command(command):
            return f"invalid validation command: {command}"

    required_text_fields = {
        "repo": request.repo,
        "base_ref": request.base_ref,
        "task_title": request.task_title,
        "task_body": request.task_body,
    }
    for field_name, value in required_text_fields.items():
        if not value.strip():
            return f"{field_name} is required."

    return None


def render_github_issue_body(request: RunnerBridgeRequest) -> str:
    allowed_files = "\n".join(f"- {path}" for path in sorted(request.allowed_files))
    protected_files = "\n".join(f"- {path}" for path in sorted(request.protected_files))
    validation_commands = "\n".join(
        f"- {command}" for command in request.validation_commands
    )

    return "\n".join(
        [
            f"Repository: {request.repo}",
            f"Base: {request.base_ref}",
            f"Approval: {request.approval_marker}",
            "",
            "Allowed files:",
            allowed_files,
            "",
            "Protected files:",
            protected_files or "- none",
            "",
            "Validation commands:",
            validation_commands,
            "",
            "```task",
            request.task_body.strip(),
            "```",
        ]
    )


def _normalize_relative_path(path: str) -> str | None:
    raw_path = path.strip()
    if not raw_path:
        return None
    pure_path = PurePosixPath(raw_path)
    if pure_path.is_absolute():
        return None
    if any(part in {"", ".", ".."} for part in pure_path.parts):
        return None
    return pure_path.as_posix()


def _is_valid_validation_command(command: str) -> bool:
    stripped_command = command.strip()
    if not stripped_command or _SHELL_META_PATTERN.search(stripped_command):
        return False

    try:
        parts = shlex.split(stripped_command, posix=True)
    except ValueError:
        return False

    if not parts:
        return False
    if "/" in parts[0] or parts[0] not in _ALLOWED_VALIDATION_TOOLS:
        return False
    return all(part not in {"&&", "||", ";"} for part in parts)
