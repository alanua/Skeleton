from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re

from core.action_gate import ALLOWED_REPOS


EVENT_SCHEMA = "skeleton.operator_event.v1"

ALLOWED_EVENT_TYPES = frozenset({"operator_console_interaction"})
ALLOWED_RESULTS = frozenset({"blocked", "dry_run", "validated"})
ALLOWED_SOURCES = frozenset({"operator_console", "telegram_callback"})

MAX_ACTION_NAME_CHARS = 64
MAX_ACTOR_REFERENCE_CHARS = 96
MAX_SUMMARY_CHARS = 240

_ACTION_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_ACTOR_REFERENCE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9@._/-]{1,63}$")
_HEAD_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@dataclass(frozen=True)
class OperatorEvent:
    """Bounded public-safe operator-console event metadata."""

    repo: str
    issue_number: int
    pr_number: int
    head_sha: str
    event_type: str
    action_name: str
    result: str
    source: str
    actor_reference: str
    timestamp: str
    summary: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "repo", _validated_repo(self.repo))
        object.__setattr__(self, "issue_number", _validated_positive_int(self.issue_number, "issue_number"))
        object.__setattr__(self, "pr_number", _validated_positive_int(self.pr_number, "pr_number"))
        object.__setattr__(self, "head_sha", _validated_head_sha(self.head_sha))
        object.__setattr__(self, "event_type", _validated_choice(self.event_type, ALLOWED_EVENT_TYPES, "event_type"))
        object.__setattr__(self, "action_name", _validated_action_name(self.action_name))
        object.__setattr__(self, "result", _validated_choice(self.result, ALLOWED_RESULTS, "result"))
        object.__setattr__(self, "source", _validated_choice(self.source, ALLOWED_SOURCES, "source"))
        object.__setattr__(self, "actor_reference", _validated_actor_reference(self.actor_reference))
        object.__setattr__(self, "timestamp", _validated_timestamp(self.timestamp))
        object.__setattr__(self, "summary", _validated_summary(self.summary))

    def to_dict(self) -> dict[str, object]:
        return operator_event_to_dict(self)

    def render_public_issue_comment(self) -> str:
        return render_public_issue_comment(self)


def operator_event_to_dict(event: OperatorEvent) -> dict[str, object]:
    """Render the deterministic JSON-compatible dry-run event record."""
    if not isinstance(event, OperatorEvent):
        raise ValueError("event must be an OperatorEvent.")

    return {
        "schema": EVENT_SCHEMA,
        "repo": event.repo,
        "issue_number": event.issue_number,
        "pr_number": event.pr_number,
        "head_sha": event.head_sha,
        "event_type": event.event_type,
        "action_name": event.action_name,
        "result": event.result,
        "source": event.source,
        "actor_reference": event.actor_reference,
        "timestamp": event.timestamp,
        "summary": event.summary,
    }


def render_public_issue_comment(event: OperatorEvent) -> str:
    """Render deterministic public-safe issue comment text for an event."""
    if not isinstance(event, OperatorEvent):
        raise ValueError("event must be an OperatorEvent.")

    return "\n".join(
        (
            "Operator event record (stage 1 dry run)",
            f"Repository: {event.repo}",
            f"Issue: #{event.issue_number}",
            f"Pull request: #{event.pr_number}",
            f"Head SHA: {event.head_sha}",
            f"Event type: {event.event_type}",
            f"Action: {event.action_name}",
            f"Result: {event.result}",
            f"Source: {event.source}",
            f"Actor reference: {event.actor_reference}",
            f"Timestamp: {event.timestamp}",
            f"Summary: {event.summary}",
        )
    )


def _validated_repo(repo: object) -> str:
    if repo not in ALLOWED_REPOS:
        raise ValueError("repo must be allowlisted for operator events.")
    return repo


def _validated_positive_int(value: object, field_name: str) -> int:
    if not _is_positive_int(value):
        raise ValueError(f"{field_name} must be a positive integer.")
    return value


def _validated_head_sha(head_sha: object) -> str:
    if not isinstance(head_sha, str) or _HEAD_SHA_RE.fullmatch(head_sha) is None:
        raise ValueError("head_sha must be a 40-character Git SHA.")
    return head_sha.lower()


def _validated_choice(value: object, allowed: frozenset[str], field_name: str) -> str:
    if value not in allowed:
        raise ValueError(f"{field_name} is not supported.")
    return value


def _validated_action_name(action_name: object) -> str:
    if (
        not isinstance(action_name, str)
        or len(action_name) > MAX_ACTION_NAME_CHARS
        or _ACTION_NAME_RE.fullmatch(action_name) is None
    ):
        raise ValueError("action_name must be a bounded public-safe action identifier.")
    return action_name


def _validated_actor_reference(actor_reference: object) -> str:
    if (
        not isinstance(actor_reference, str)
        or len(actor_reference) > MAX_ACTOR_REFERENCE_CHARS
        or _ACTOR_REFERENCE_RE.fullmatch(actor_reference) is None
    ):
        raise ValueError("actor_reference must be a bounded public-safe actor reference.")
    return actor_reference


def _validated_timestamp(timestamp: object) -> str:
    if not isinstance(timestamp, str) or _UTC_TIMESTAMP_RE.fullmatch(timestamp) is None:
        raise ValueError("timestamp must be a UTC RFC3339 second timestamp.")
    try:
        parsed = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError("timestamp must be a UTC RFC3339 second timestamp.") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("timestamp must be a UTC RFC3339 second timestamp.")
    return timestamp


def _validated_summary(summary: object) -> str:
    if not isinstance(summary, str):
        raise ValueError("summary must be text.")
    normalized = " ".join(summary.split())
    if not normalized:
        raise ValueError("summary must not be empty.")
    if len(normalized) > MAX_SUMMARY_CHARS:
        raise ValueError(f"summary must be at most {MAX_SUMMARY_CHARS} characters.")
    return normalized


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
