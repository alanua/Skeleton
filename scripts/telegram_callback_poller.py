from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Any, Mapping
import urllib.parse
import urllib.request


REPO = "alanua/Skeleton"
GITHUB_API_BASE = "https://api.github.com"
TELEGRAM_API_BASE = "https://api.telegram.org"
HTTP_TIMEOUT_SECONDS = 15
TELEGRAM_CALLBACK_DATA_LIMIT = 64

_CALLBACK_RE = re.compile(
    r"^tpr1:(?P<action>approve|reject|details):p(?P<pr_number>[1-9][0-9]{0,9}):"
    r"(?P<head_marker>[0-9a-f]{8}|nosha):(?P<digest>[0-9a-f]{12})$"
)


@dataclass(frozen=True)
class ParsedCallback:
    action: str
    pr_number: int
    head_marker: str
    digest: str


def parse_callback_data(callback_data: object) -> ParsedCallback:
    """Parse one bounded PR Telegram callback value from the PR notification buttons."""
    if not isinstance(callback_data, str):
        raise ValueError("callback_data must be text.")
    if not callback_data or len(callback_data.encode("utf-8")) > TELEGRAM_CALLBACK_DATA_LIMIT:
        raise ValueError("callback_data must be a bounded Telegram callback value.")

    match = _CALLBACK_RE.fullmatch(callback_data)
    if match is None:
        raise ValueError("callback_data does not match the tpr1 callback format.")

    return ParsedCallback(
        action=match.group("action"),
        pr_number=int(match.group("pr_number")),
        head_marker=match.group("head_marker"),
        digest=match.group("digest"),
    )


def render_audit_comment(
    parsed: ParsedCallback,
    *,
    repo: str = REPO,
    result: str = "recorded",
) -> str:
    """Render bounded public-safe audit text for one parsed button click."""
    if result not in {"recorded", "blocked"}:
        raise ValueError("audit comment result is not supported.")

    return "\n".join(
        (
            "Operator event record (Telegram callback stage 1)",
            f"Repository: {repo}",
            f"Pull request: #{parsed.pr_number}",
            f"Action: telegram_{parsed.action}",
            f"Head marker: {parsed.head_marker}",
            f"Callback digest: {parsed.digest}",
            f"Result: {result}",
            "Summary: Stage 1 recorded the inline button click only; no repository action was executed.",
        )
    )


def handle_callback_query(
    callback_query: Mapping[str, object],
    *,
    repo: str = REPO,
    dry_run: bool = False,
) -> dict[str, object]:
    """Handle one Telegram callback query as a comment-only stage 1 audit event."""
    callback_id = _bounded_callback_id(callback_query.get("id"))
    try:
        parsed = parse_callback_data(callback_query.get("data"))
    except ValueError as exc:
        result = _result(
            status="blocked",
            reason=str(exc),
            github="not_called",
            posted=False,
            comment=None,
        )
        return _answer_callback_query(result, callback_id, dry_run=dry_run)

    comment = render_audit_comment(parsed, repo=repo)
    if repo != REPO:
        result = _result(
            status="blocked",
            reason=f"repo must be {REPO}.",
            github="not_called",
            posted=False,
            comment=render_audit_comment(parsed, repo=REPO, result="blocked"),
        )
        return _answer_callback_query(result, callback_id, dry_run=dry_run)

    if dry_run:
        result = _result(
            status="dry_run",
            reason="dry_run enabled; no HTTP calls were made.",
            github="not_called",
            posted=False,
            comment=comment,
        )
        return _answer_callback_query(result, callback_id, dry_run=True)

    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        result = _result(
            status="skipped",
            reason="GITHUB_TOKEN is missing; no audit comment was posted.",
            github="skipped_missing_token",
            posted=False,
            comment=comment,
        )
        return _answer_callback_query(result, callback_id, dry_run=False)

    pr_state = _fetch_pr_state(parsed.pr_number, github_token)
    block_reason = _head_binding_block_reason(parsed, pr_state)
    if block_reason is not None:
        result = _result(
            status="blocked",
            reason=block_reason,
            github="pr_state_checked",
            posted=False,
            comment=render_audit_comment(parsed, result="blocked"),
        )
        return _answer_callback_query(result, callback_id, dry_run=False)

    _post_pr_comment(parsed.pr_number, comment, github_token)
    result = _result(
        status="comment_posted",
        reason="audit comment posted.",
        github="comment_posted",
        posted=True,
        comment=comment,
    )
    return _answer_callback_query(result, callback_id, dry_run=False)


def _result(
    *,
    status: str,
    reason: str,
    github: str,
    posted: bool,
    comment: str | None,
) -> dict[str, object]:
    return {
        "status": status,
        "reason": reason,
        "github": github,
        "comment_posted": posted,
        "comment": comment,
        "telegram_answer": "not_called",
    }


def _bounded_callback_id(callback_id: object) -> str | None:
    if not isinstance(callback_id, str) or not callback_id:
        return None
    if len(callback_id.encode("utf-8")) > TELEGRAM_CALLBACK_DATA_LIMIT:
        return None
    return callback_id


def _head_binding_block_reason(parsed: ParsedCallback, pr_state: Mapping[str, object]) -> str | None:
    if parsed.action not in {"approve", "reject"}:
        return None

    if parsed.head_marker == "nosha":
        return "approve/reject callback requires a SHA head marker."

    if pr_state.get("number") != parsed.pr_number:
        return "approve/reject callback PR number does not match GitHub PR state."

    head = pr_state.get("head")
    head_sha = head.get("sha") if isinstance(head, Mapping) else None
    if not isinstance(head_sha, str) or not head_sha.lower().startswith(parsed.head_marker):
        return "approve/reject callback head marker does not match GitHub PR state."
    return None


def _fetch_pr_state(pr_number: int, github_token: str) -> Mapping[str, object]:
    payload = _github_json_request(
        f"/repos/{REPO}/pulls/{pr_number}",
        github_token,
        method="GET",
    )
    if not isinstance(payload, Mapping):
        raise RuntimeError("GitHub PR response was not an object.")
    return payload


def _post_pr_comment(pr_number: int, comment: str, github_token: str) -> None:
    _github_json_request(
        f"/repos/{REPO}/issues/{pr_number}/comments",
        github_token,
        method="POST",
        payload={"body": comment},
    )


def _github_json_request(
    path: str,
    github_token: str,
    *,
    method: str,
    payload: Mapping[str, object] | None = None,
) -> Any:
    data = None
    if payload is not None:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        f"{GITHUB_API_BASE}{path}",
        data=data,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {github_token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method=method,
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        response_body = response.read()
    if not response_body:
        return None
    return json.loads(response_body.decode("utf-8"))


def _answer_callback_query(
    result: dict[str, object],
    callback_id: str | None,
    *,
    dry_run: bool,
) -> dict[str, object]:
    if dry_run:
        result["telegram_answer"] = "not_called_dry_run"
        return result

    bot_token = os.environ.get("SKELETON_TG_BOT")
    if not bot_token or callback_id is None:
        result["telegram_answer"] = "skipped"
        return result

    payload = urllib.parse.urlencode({"callback_query_id": callback_id}).encode("utf-8")
    request = urllib.request.Request(
        f"{TELEGRAM_API_BASE}/bot{bot_token}/answerCallbackQuery",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS):
        pass
    result["telegram_answer"] = "answered"
    return result
