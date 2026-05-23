from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional


ALLOWED_ACTION_TYPES = frozenset({"merge_pull_request"})
ALLOWED_REPOS = frozenset({"alanua/Skeleton", "alanua/bauclock", "alanua/Lavalamp"})

_HEAD_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class ActionGateRequest:
    action_type: str
    repo: str
    pr_number: int
    expected_head_sha: str
    expected_files: tuple[str, ...]
    user_approved: bool


@dataclass(frozen=True)
class ActionGateDecision:
    status: str
    action_type: str
    repo: str
    pr_number: Optional[int]
    reasons: tuple[str, ...]


def validate_action_request(request: ActionGateRequest) -> ActionGateDecision:
    """Validate a future approved repository action without performing it."""
    reasons = tuple(_validation_reasons(request))
    return ActionGateDecision(
        status="blocked" if reasons else "allowed",
        action_type=request.action_type,
        repo=request.repo,
        pr_number=request.pr_number if _is_positive_int(request.pr_number) else None,
        reasons=reasons,
    )


def _validation_reasons(request: ActionGateRequest) -> list[str]:
    reasons: list[str] = []

    if request.action_type not in ALLOWED_ACTION_TYPES:
        reasons.append("action_type is not allowlisted.")

    if request.repo not in ALLOWED_REPOS:
        reasons.append("repo is not allowlisted.")

    if not _is_positive_int(request.pr_number):
        reasons.append("pr_number must be a positive integer.")

    if not isinstance(request.expected_head_sha, str) or not _HEAD_SHA_RE.fullmatch(request.expected_head_sha):
        reasons.append("expected_head_sha must be a 40-character Git SHA.")

    reasons.extend(_expected_file_reasons(request.expected_files))

    if request.user_approved is not True:
        reasons.append("user_approved must be true.")

    return reasons


def _expected_file_reasons(expected_files: tuple[str, ...]) -> list[str]:
    if not isinstance(expected_files, tuple) or not expected_files:
        return ["expected_files must be a non-empty tuple of repository-relative paths."]

    seen: set[str] = set()
    for expected_file in expected_files:
        if not _is_safe_expected_file(expected_file):
            return ["expected_files must contain safe repository-relative paths."]
        if expected_file in seen:
            return ["expected_files must not contain duplicates."]
        seen.add(expected_file)

    return []


def _is_safe_expected_file(expected_file: object) -> bool:
    if not isinstance(expected_file, str) or expected_file.strip() != expected_file or expected_file == "":
        return False

    parts = expected_file.split("/")
    return (
        not expected_file.startswith("/")
        and "\\" not in expected_file
        and all(part not in {"", ".", ".."} for part in parts)
    )


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
