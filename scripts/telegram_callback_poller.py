from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Any, Mapping
import urllib.request


REPO = "alanua/Skeleton"
GITHUB_API_BASE = "https://api.github.com"
TELEGRAM_API_BASE = "https://api.telegram.org"
HTTP_TIMEOUT_SECONDS = 10
ALLOWED_ACTIONS = frozenset({"approve", "reject", "details"})

_CALLBACK_RE = re.compile(
    r"^tpr1:(?P<action>approve|reject|details):p(?P<pr_number>[1-9][0-9]*):"
    r"(?P<head_marker>[0-9a-f]{8}):(?P<digest>[0-9a-f]{12})$"
)
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class CallbackData:
    action: str
    pr_number: int
    head_marker: str
    digest: str


def parse_callback_data(callback_data: object) -> CallbackData:
    """Parse the public compact callback binding emitted by PR review buttons."""
    if not isinstance(callback_data, str):
        raise ValueError("callback_data must be text.")

    match = _CALLBACK_RE.fullmatch(callback_data)
    if match is None:
        raise ValueError("callback_data must match the bounded tpr1 callback format.")

    action = match.group("action")
    if action not in ALLOWED_ACTIONS:
        raise ValueError("callback_data action is not supported.")

    return CallbackData(
        action=action,
        pr_number=int(match.group("pr_number")),
        head_marker=match.group("head_marker"),
        digest=match.group("digest"),
    )


def render_audit_comment(callback: CallbackData, *, validation: str) -> str:
    """Render bounded public-safe GitHub conversation text for one callback."""
    return "\n".join(
        (
            "Operator event record (Telegram callback stage 1)",
            f"Repository: {REPO}",
            f"Pull request: #{callback.pr_number}",
            f"Action: telegram_{callback.action}",
            f"Callback head marker: {callback.head_marker}",
            f"Validation: {validation}",
            "Result: audit comment only; no repository action performed.",
            "Source: telegram_callback",
        )
    )


def handle_callback_query(
    callback_query: Mapping[str, Any],
    *,
    repo: str = REPO,
    dry_run: bool = False,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Validate one Telegram callback and optionally post its audit comment."""
    env = os.environ if environ is None else environ
    query_id = _optional_text(callback_query.get("id"))
    parsed: CallbackData | None = None
    comment: str | None = None
    result: dict[str, object] = {
        "status": "blocked",
        "repo": repo,
        "comment_status": "not_posted",
        "telegram_answer_status": "not_answered",
    }

    try:
        parsed = parse_callback_data(callback_query.get("data"))
        result.update(
            {
                "action": parsed.action,
                "pr_number": parsed.pr_number,
                "head_marker": parsed.head_marker,
            }
        )
    except ValueError as exc:
        result["reason"] = str(exc)
        return _finish_callback(result, query_id, env, dry_run=dry_run)

    if repo != REPO:
        result["reason"] = "repository is not allowlisted for Telegram callback auditing."
        return _finish_callback(result, query_id, env, dry_run=dry_run)

    comment = render_audit_comment(parsed, validation="dry_run" if dry_run else "validated")
    result["comment"] = comment
    if dry_run:
        result.update({"status": "dry_run", "comment_status": "dry_run"})
        return _finish_callback(result, query_id, env, dry_run=True)

    github_token = env.get("GITHUB_TOKEN")
    if not github_token:
        result.update(
            {
                "status": "skipped",
                "reason": "GITHUB_TOKEN is missing; audit comment was not posted.",
                "comment_status": "skipped_no_github_token",
            }
        )
        return _finish_callback(result, query_id, env, dry_run=False)

    pr_state = _fetch_pull_request(parsed.pr_number, github_token)
    current_pr_number = pr_state.get("number")
    if current_pr_number != parsed.pr_number:
        result["reason"] = "GitHub pull request number did not match callback binding."
        return _finish_callback(result, query_id, env, dry_run=False)

    if parsed.action in {"approve", "reject"}:
        current_head_sha = _pull_request_head_sha(pr_state)
        if current_head_sha is None or not current_head_sha.startswith(parsed.head_marker):
            result["reason"] = "GitHub pull request head marker did not match callback binding."
            return _finish_callback(result, query_id, env, dry_run=False)

    _post_audit_comment(parsed.pr_number, comment, github_token)
    result.update({"status": "posted", "comment_status": "posted"})
    return _finish_callback(result, query_id, env, dry_run=False)


def handle_update(
    update: Mapping[str, Any],
    *,
    repo: str = REPO,
    dry_run: bool = False,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    callback_query = update.get("callback_query")
    if not isinstance(callback_query, Mapping):
        return {
            "status": "ignored",
            "repo": repo,
            "comment_status": "not_posted",
            "telegram_answer_status": "not_answered",
            "reason": "update has no callback_query.",
        }
    return handle_callback_query(
        callback_query,
        repo=repo,
        dry_run=dry_run,
        environ=environ,
    )


def _finish_callback(
    result: dict[str, object],
    callback_query_id: str | None,
    env: Mapping[str, str],
    *,
    dry_run: bool,
) -> dict[str, object]:
    if dry_run:
        result["telegram_answer_status"] = "dry_run"
    elif callback_query_id and env.get("SKELETON_TG_BOT"):
        _answer_callback_query(callback_query_id, env["SKELETON_TG_BOT"])
        result["telegram_answer_status"] = "answered"
    else:
        result["telegram_answer_status"] = "skipped_no_bot_token"
    return result


def _fetch_pull_request(pr_number: int, token: str) -> dict[str, Any]:
    response = _github_json_request(
        f"{GITHUB_API_BASE}/repos/{REPO}/pulls/{pr_number}",
        token,
    )
    if not isinstance(response, dict):
        raise ValueError("GitHub pull request response must be an object.")
    return response


def _post_audit_comment(pr_number: int, comment: str, token: str) -> None:
    _github_json_request(
        f"{GITHUB_API_BASE}/repos/{REPO}/issues/{pr_number}/comments",
        token,
        payload={"body": comment},
    )


def _github_json_request(
    url: str,
    token: str,
    *,
    payload: dict[str, str] | None = None,
) -> object:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET" if data is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        raw_body = response.read()
    return json.loads(raw_body.decode("utf-8")) if raw_body else {}


def _answer_callback_query(callback_query_id: str, bot_token: str) -> None:
    data = json.dumps({"callback_query_id": callback_query_id}).encode("utf-8")
    request = urllib.request.Request(
        f"{TELEGRAM_API_BASE}/bot{bot_token}/answerCallbackQuery",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS):
        pass


def _pull_request_head_sha(pr_state: Mapping[str, Any]) -> str | None:
    head = pr_state.get("head")
    sha = head.get("sha") if isinstance(head, Mapping) else None
    if not isinstance(sha, str) or _SHA_RE.fullmatch(sha) is None:
        return None
    return sha.lower()


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
