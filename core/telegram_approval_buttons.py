from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, Optional
from urllib.parse import urlparse

from core.action_gate import (
    ALLOWED_REPOS,
    ActionGateDecision,
    ActionGateRequest,
    validate_action_request,
)


CARD_SCHEMA = "skeleton.telegram_approval_buttons.card.v1"
CALLBACK_SCHEMA = "skeleton.telegram_approval_buttons.callback.v1"
CARD_KIND = "completed_pr_review"

BUTTON_ACTIONS = ("approve", "reject", "details", "open_pr")
CALLBACK_KEYS = frozenset({"schema", "action", "repo", "pr_number", "head_sha"})

MAX_CHANGED_FILES = 20
MAX_FILE_PATH_CHARS = 180
MAX_SUMMARY_CHARS = 240
MAX_URL_CHARS = 512

_HEAD_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class TelegramCallbackDecision:
    status: str
    action: Optional[str]
    repo: Optional[str]
    pr_number: Optional[int]
    reasons: tuple[str, ...]
    action_gate_decision: Optional[ActionGateDecision] = None


def build_pr_ready_card_payload(
    *,
    repo: str,
    pr_number: int,
    head_sha: str,
    changed_files: tuple[str, ...],
    test_summary: str,
    risk_summary: str,
    pr_url: str,
) -> dict[str, object]:
    """Build a deterministic public-safe dry-run Telegram PR review card."""
    normalized_repo = _validated_repo(repo)
    normalized_pr_number = _validated_pr_number(pr_number)
    normalized_head_sha = _validated_head_sha(head_sha)
    normalized_files = _validated_changed_files(changed_files)
    normalized_url = _validated_pr_url(pr_url)
    normalized_tests = _bounded_summary(test_summary, "test_summary")
    normalized_risk = _bounded_summary(risk_summary, "risk_summary")

    visible_files = normalized_files[:MAX_CHANGED_FILES]
    callback_base = {
        "schema": CALLBACK_SCHEMA,
        "repo": normalized_repo,
        "pr_number": normalized_pr_number,
        "head_sha": normalized_head_sha,
    }
    buttons = [
        {
            "action": action,
            "label": label,
            "callback_payload": {**callback_base, "action": action},
            **({"url": normalized_url} if action == "open_pr" else {}),
        }
        for action, label in (
            ("approve", "Approve"),
            ("reject", "Reject"),
            ("details", "Details"),
            ("open_pr", "Open PR"),
        )
    ]

    return {
        "schema": CARD_SCHEMA,
        "kind": CARD_KIND,
        "repo": normalized_repo,
        "pr_number": normalized_pr_number,
        "head_sha": normalized_head_sha,
        "pr_url": normalized_url,
        "changed_files": list(visible_files),
        "changed_files_total": len(normalized_files),
        "changed_files_truncated": len(visible_files) != len(normalized_files),
        "test_summary": normalized_tests,
        "risk_summary": normalized_risk,
        "text": _render_card_text(
            repo=normalized_repo,
            pr_number=normalized_pr_number,
            head_sha=normalized_head_sha,
            changed_files=visible_files,
            changed_files_total=len(normalized_files),
            test_summary=normalized_tests,
            risk_summary=normalized_risk,
        ),
        "buttons": buttons,
    }


def validate_callback_payload(
    payload: Mapping[str, object],
    *,
    current_head_sha: str,
    expected_files: tuple[str, ...],
) -> TelegramCallbackDecision:
    """Validate one dry-run Telegram callback before any action-gate handoff."""
    callback, reasons = _validated_callback(payload, current_head_sha=current_head_sha)
    if reasons:
        return _blocked_callback(callback, reasons)

    assert callback is not None
    action = callback["action"]
    repo = callback["repo"]
    pr_number = callback["pr_number"]
    head_sha = callback["head_sha"]
    assert isinstance(action, str)
    assert isinstance(repo, str)
    assert isinstance(pr_number, int)
    assert isinstance(head_sha, str)

    if action != "approve":
        return TelegramCallbackDecision(
            status="validated",
            action=action,
            repo=repo,
            pr_number=pr_number,
            reasons=(),
        )

    gate_decision = validate_action_request(
        ActionGateRequest(
            action_type="merge_pull_request",
            repo=repo,
            pr_number=pr_number,
            expected_head_sha=head_sha,
            expected_files=expected_files,
            user_approved=True,
        )
    )
    return TelegramCallbackDecision(
        status="validated" if gate_decision.status == "allowed" else "blocked",
        action=action,
        repo=repo,
        pr_number=pr_number,
        reasons=gate_decision.reasons,
        action_gate_decision=gate_decision,
    )


def _validated_callback(
    payload: Mapping[str, object],
    *,
    current_head_sha: str,
) -> tuple[Optional[dict[str, object]], tuple[str, ...]]:
    if not isinstance(payload, Mapping):
        return None, ("callback payload must be a mapping.",)

    callback = dict(payload)
    reasons: list[str] = []
    if set(callback) != CALLBACK_KEYS:
        reasons.append("callback payload fields are malformed.")

    if callback.get("schema") != CALLBACK_SCHEMA:
        reasons.append("callback schema is not supported.")

    if callback.get("action") not in BUTTON_ACTIONS:
        reasons.append("callback action is not supported.")

    repo = callback.get("repo")
    if repo not in ALLOWED_REPOS:
        reasons.append("callback repo is not allowlisted.")

    if not _is_positive_int(callback.get("pr_number")):
        reasons.append("callback pr_number must be a positive integer.")

    head_sha = callback.get("head_sha")
    if not _is_head_sha(head_sha):
        reasons.append("callback head_sha must be a 40-character Git SHA.")

    if not _is_head_sha(current_head_sha):
        reasons.append("current_head_sha must be a 40-character Git SHA.")
    elif _is_head_sha(head_sha) and str(head_sha).lower() != current_head_sha.lower():
        reasons.append("callback head_sha is stale.")

    return callback, tuple(reasons)


def _blocked_callback(
    callback: Optional[dict[str, object]],
    reasons: tuple[str, ...],
) -> TelegramCallbackDecision:
    action = callback.get("action") if callback is not None else None
    repo = callback.get("repo") if callback is not None else None
    pr_number = callback.get("pr_number") if callback is not None else None
    return TelegramCallbackDecision(
        status="blocked",
        action=action if isinstance(action, str) else None,
        repo=repo if isinstance(repo, str) else None,
        pr_number=pr_number if _is_positive_int(pr_number) else None,
        reasons=reasons,
    )


def _render_card_text(
    *,
    repo: str,
    pr_number: int,
    head_sha: str,
    changed_files: tuple[str, ...],
    changed_files_total: int,
    test_summary: str,
    risk_summary: str,
) -> str:
    files_label = f"Changed files ({len(changed_files)}/{changed_files_total} shown):"
    files = "\n".join(f"- {path}" for path in changed_files)
    return "\n".join(
        (
            "PR ready for operator review",
            f"Repository: {repo}",
            f"PR: #{pr_number}",
            f"Head SHA: {head_sha}",
            files_label,
            files,
            f"Tests: {test_summary}",
            f"Risk: {risk_summary}",
        )
    )


def _validated_repo(repo: object) -> str:
    if repo not in ALLOWED_REPOS:
        raise ValueError("repo must be allowlisted for Telegram approval buttons.")
    return repo


def _validated_pr_number(pr_number: object) -> int:
    if not _is_positive_int(pr_number):
        raise ValueError("pr_number must be a positive integer.")
    return pr_number


def _validated_head_sha(head_sha: object) -> str:
    if not _is_head_sha(head_sha):
        raise ValueError("head_sha must be a 40-character Git SHA.")
    return head_sha.lower()


def _validated_changed_files(changed_files: object) -> tuple[str, ...]:
    if not isinstance(changed_files, tuple) or not changed_files:
        raise ValueError("changed_files must be a non-empty tuple of repository-relative paths.")

    normalized = tuple(sorted(changed_files))
    if len(set(normalized)) != len(normalized):
        raise ValueError("changed_files must not contain duplicates.")
    if any(not _is_safe_file_path(path) for path in normalized):
        raise ValueError("changed_files must contain bounded safe repository-relative paths.")
    return normalized


def _validated_pr_url(pr_url: object) -> str:
    if not isinstance(pr_url, str) or len(pr_url) > MAX_URL_CHARS or pr_url.strip() != pr_url:
        raise ValueError("pr_url must be a bounded HTTPS URL.")
    parsed = urlparse(pr_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("pr_url must be a bounded HTTPS URL.")
    return pr_url


def _bounded_summary(summary: object, field_name: str) -> str:
    if not isinstance(summary, str):
        raise ValueError(f"{field_name} must be text.")
    normalized = " ".join(summary.split())
    if not normalized:
        raise ValueError(f"{field_name} must not be empty.")
    if len(normalized) <= MAX_SUMMARY_CHARS:
        return normalized
    return normalized[: MAX_SUMMARY_CHARS - 3] + "..."


def _is_head_sha(value: object) -> bool:
    return isinstance(value, str) and _HEAD_SHA_RE.fullmatch(value) is not None


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_safe_file_path(path: object) -> bool:
    if (
        not isinstance(path, str)
        or path.strip() != path
        or not path
        or len(path) > MAX_FILE_PATH_CHARS
    ):
        return False

    parts = path.split("/")
    return (
        not path.startswith("/")
        and "\\" not in path
        and all(part not in {"", ".", ".."} for part in parts)
    )
