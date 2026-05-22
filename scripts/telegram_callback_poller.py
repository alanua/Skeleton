from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request


REPO = "alanua/Skeleton"
GITHUB_API_BASE = "https://api.github.com"
TELEGRAM_API_BASE = "https://api.telegram.org"
HTTP_TIMEOUT_SECONDS = 15
TELEGRAM_CALLBACK_DATA_LIMIT = 64
TELEGRAM_UPDATE_LIMIT = 25
CALLBACK_ID_HISTORY_LIMIT = 500
RUNNER_READY_LABEL = "runner:ready"
GITHUB_ACTION_REQUEST_MODE = "GITHUB_ACTION_REQUEST"
MERGE_ACTION = "merge_pr_squash"
CHATGPT_REVIEW_DECISION = "ChatGPT review decision: CONTENT APPROVED"
DEFAULT_CALLBACK_STATE_PATH = Path(
    "/home/agent/agent-dev/state/telegram_callback_poller.json"
)

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

    if parsed.action == "approve":
        review_block_reason = _review_block_reason(
            parsed,
            pr_state,
            _fetch_pr_comments(parsed.pr_number, github_token),
        )
        if review_block_reason is not None:
            result = _result(
                status="blocked",
                reason=review_block_reason,
                github="chatgpt_review_checked",
                posted=False,
                comment=render_audit_comment(parsed, result="blocked"),
            )
            return _answer_callback_query(result, callback_id, dry_run=False)

        action_request = _create_merge_action_request(parsed, pr_state, github_token)
        _post_pr_comment(parsed.pr_number, comment, github_token)
        result = _result(
            status="action_request_created",
            reason="reviewed Telegram approve queued a Runner merge action request.",
            github="action_request_created",
            posted=True,
            comment=comment,
        )
        result["action_request"] = action_request
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


def poll_once(*, state_path: Path | None = None) -> dict[str, object]:
    """Read one bounded Telegram update batch and handle callback queries only."""
    path = state_path or _callback_state_path()
    bot_token = os.environ.get("SKELETON_TG_BOT")
    if not bot_token:
        return {
            "status": "skipped_missing_telegram_token",
            "updates_seen": 0,
            "callbacks_seen": 0,
            "callbacks_handled": 0,
            "offset": _read_offset(path),
        }

    state = _read_state(path)
    offset = state["offset"]
    handled_callback_ids = list(state["handled_callback_ids"])
    handled_callback_id_set = set(handled_callback_ids)
    updates = _get_updates(bot_token, offset)
    next_offset = offset
    callbacks_seen = 0
    callbacks_handled = 0
    callbacks_duplicate = 0

    for update in updates:
        if not isinstance(update, Mapping):
            continue

        update_id = update.get("update_id")
        if isinstance(update_id, int) and update_id >= 0:
            candidate_offset = update_id + 1
            next_offset = candidate_offset if next_offset is None else max(next_offset, candidate_offset)

        callback_query = update.get("callback_query")
        if not isinstance(callback_query, Mapping):
            continue

        callbacks_seen += 1
        callback_id = _bounded_callback_id(callback_query.get("id"))
        if callback_id is not None and callback_id in handled_callback_id_set:
            _handle_duplicate_callback_query(callback_query)
            callbacks_duplicate += 1
        else:
            handle_callback_query(callback_query)
            if callback_id is not None:
                handled_callback_ids = _remember_callback_id(
                    handled_callback_ids, callback_id
                )
                handled_callback_id_set = set(handled_callback_ids)
        callbacks_handled += 1

    if next_offset != offset or handled_callback_ids != state["handled_callback_ids"]:
        _write_state(path, next_offset, handled_callback_ids)

    return {
        "status": "polled",
        "updates_seen": len(updates),
        "callbacks_seen": callbacks_seen,
        "callbacks_handled": callbacks_handled,
        "callbacks_duplicate": callbacks_duplicate,
        "offset": next_offset,
    }


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


def _callback_state_path() -> Path:
    configured = os.environ.get("SKELETON_TG_CALLBACK_STATE")
    return Path(configured) if configured else DEFAULT_CALLBACK_STATE_PATH


def _read_offset(path: Path) -> int | None:
    return _read_state(path)["offset"]


def _read_state(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"offset": None, "handled_callback_ids": []}

    offset = payload.get("offset") if isinstance(payload, Mapping) else None
    handled_callback_ids = (
        payload.get("handled_callback_ids") if isinstance(payload, Mapping) else None
    )
    return {
        "offset": offset if isinstance(offset, int) and offset >= 0 else None,
        "handled_callback_ids": _bounded_callback_id_history(handled_callback_ids),
    }


def _write_offset(path: Path, offset: int) -> None:
    _write_state(path, offset, _read_state(path)["handled_callback_ids"])


def _write_state(
    path: Path, offset: int | None, handled_callback_ids: object
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    payload: dict[str, object] = {
        "handled_callback_ids": _bounded_callback_id_history(handled_callback_ids)
    }
    if offset is not None:
        payload["offset"] = offset
    temporary.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _bounded_callback_id_history(callback_ids: object) -> list[str]:
    if not isinstance(callback_ids, list):
        return []

    history: list[str] = []
    for callback_id in callback_ids:
        bounded = _bounded_callback_id(callback_id)
        if bounded is None or bounded in history:
            continue
        history.append(bounded)
    return history[-CALLBACK_ID_HISTORY_LIMIT:]


def _remember_callback_id(callback_ids: list[str], callback_id: str) -> list[str]:
    return _bounded_callback_id_history([*callback_ids, callback_id])


def _get_updates(bot_token: str, offset: int | None) -> list[object]:
    query: dict[str, object] = {
        "allowed_updates": json.dumps(["callback_query"], separators=(",", ":")),
        "limit": TELEGRAM_UPDATE_LIMIT,
        "timeout": 0,
    }
    if offset is not None:
        query["offset"] = offset

    request = urllib.request.Request(
        f"{TELEGRAM_API_BASE}/bot{bot_token}/getUpdates?{urllib.parse.urlencode(query)}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))

    result = payload.get("result") if isinstance(payload, Mapping) else None
    return result if isinstance(result, list) else []


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


def _fetch_pr_comments(pr_number: int, github_token: str) -> list[object]:
    payload = _github_json_request(
        f"/repos/{REPO}/issues/{pr_number}/comments",
        github_token,
        method="GET",
    )
    return payload if isinstance(payload, list) else []


def _review_block_reason(
    parsed: ParsedCallback,
    pr_state: Mapping[str, object],
    comments: list[object],
) -> str | None:
    head = pr_state.get("head")
    head_sha = head.get("sha") if isinstance(head, Mapping) else None
    if not isinstance(head_sha, str) or not _has_matching_review_marker(
        comments,
        parsed.pr_number,
        head_sha,
        parsed.head_marker,
    ):
        return (
            "Telegram approve requires a ChatGPT CONTENT APPROVED review marker "
            "for this PR and current head."
        )
    return None


def _has_matching_review_marker(
    comments: list[object],
    pr_number: int,
    head_sha: str,
    head_marker: str | None = None,
) -> bool:
    expected_sha = head_sha.lower()
    expected_marker = (head_marker or expected_sha[:8]).lower()
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        return False

    for comment in comments:
        body = comment.get("body") if isinstance(comment, Mapping) else None
        if not isinstance(body, str) or CHATGPT_REVIEW_DECISION not in body:
            continue
        if re.search(
            rf"^Pull request:\s*#{pr_number}\s*$",
            body,
            re.MULTILINE,
        ) is None:
            continue
        sha_match = re.search(
            r"^Head SHA:\s*(?P<sha>[0-9a-fA-F]{40})\s*$",
            body,
            re.MULTILINE,
        )
        if sha_match is not None and sha_match.group("sha").lower() == expected_sha:
            return True
        marker_match = re.search(
            r"^Head marker:\s*(?P<marker>[0-9a-fA-F]{8})\s*$",
            body,
            re.MULTILINE,
        )
        if (
            marker_match is not None
            and marker_match.group("marker").lower() == expected_marker
            and expected_sha.startswith(expected_marker)
        ):
            return True
    return False


def _create_merge_action_request(
    parsed: ParsedCallback,
    pr_state: Mapping[str, object],
    github_token: str,
) -> Mapping[str, object]:
    head = pr_state.get("head")
    head_sha = head.get("sha") if isinstance(head, Mapping) else None
    if not isinstance(head_sha, str):
        raise RuntimeError("GitHub PR head SHA was unavailable for action request.")
    payload = _github_json_request(
        f"/repos/{REPO}/issues",
        github_token,
        method="POST",
        payload={
            "title": f"Runner GitHub action: squash merge PR #{parsed.pr_number}",
            "body": _render_merge_action_request(parsed.pr_number, head_sha),
            "labels": [RUNNER_READY_LABEL],
        },
    )
    if not isinstance(payload, Mapping):
        raise RuntimeError("GitHub action request issue response was not an object.")
    return {
        "number": payload.get("number"),
        "url": payload.get("html_url") or payload.get("url"),
    }


def _render_merge_action_request(pr_number: int, head_sha: str) -> str:
    return "\n".join(
        (
            f"Mode: {GITHUB_ACTION_REQUEST_MODE}",
            f"Action: {MERGE_ACTION}",
            f"Repository: {REPO}",
            f"Pull request: #{pr_number}",
            f"Expected head SHA: {head_sha}",
            "Approved via: Telegram",
        )
    )


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
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS):
            pass
    except urllib.error.HTTPError:
        result["telegram_answer"] = "error"
        result["telegram_answer_error"] = "http_error"
        return result
    except urllib.error.URLError:
        result["telegram_answer"] = "error"
        result["telegram_answer_error"] = "url_error"
        return result
    result["telegram_answer"] = "answered"
    return result


def _handle_duplicate_callback_query(
    callback_query: Mapping[str, object],
) -> dict[str, object]:
    result = _result(
        status="duplicate",
        reason="callback already processed locally; no audit comment was posted.",
        github="skipped_duplicate",
        posted=False,
        comment=None,
    )
    return _answer_callback_query(
        result,
        _bounded_callback_id(callback_query.get("id")),
        dry_run=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read one bounded Telegram callback update batch and record audit comments."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one poll pass and exit. This is the default runtime mode.",
    )
    args = parser.parse_args()
    del args

    summary = poll_once()
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
