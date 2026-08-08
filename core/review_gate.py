from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Final

from core.scheduler_models import (
    ScheduleSpec,
    build_execution_proposal,
    stable_occurrence_id,
)
from core.scheduler_store import SchedulerStore


INTERNAL_REVIEW_ROUTE: Final = "runner.internal_review"
REPAIR_ROUTE: Final = "runner.internal_review_repair"
MERGE_ROUTE: Final = "runner.internal_review_merge"
NEEDS_OPERATOR_ROUTE: Final = "runner.needs_operator"
REVIEW_VERDICTS: Final = frozenset(
    {"APPROVE", "REQUEST_CHANGES", "DO_NOT_MERGE", "NEEDS_OPERATOR"}
)
_HEAD_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class ReviewContinuation:
    status: str
    schedule_id: str
    occurrence_id: str
    created: bool
    next_step: str
    payload: Mapping[str, Any]


def ensure_draft_pr_review_continuation(
    store: SchedulerStore,
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    source_issue: int,
    allowed_files: Sequence[str],
    now: int,
) -> ReviewContinuation:
    payload = _base_payload(
        repository=repository,
        pr_number=pr_number,
        head_sha=head_sha,
        source_issue=source_issue,
        allowed_files=allowed_files,
        next_step="internal_review",
    )
    payload.update(
        {
            "review_scope": (
                "current_pr",
                "current_issue",
                "current_diff",
                "current_files",
                "validation",
                "mergeability",
                "scope",
                "privacy",
                "protected_policy",
            ),
            "requires_chat_plus": False,
        }
    )
    return _enqueue_runner_control(
        store,
        route_id=INTERNAL_REVIEW_ROUTE,
        payload=payload,
        now=now,
    )


def continue_from_internal_review_verdict(
    store: SchedulerStore,
    *,
    verdict: str,
    repository: str,
    pr_number: int,
    expected_head_sha: str,
    observed_head_sha: str,
    permitted_merge_method: str,
    policy_reason: str,
    source_issue: int,
    allowed_files: Sequence[str],
    now: int,
    protected: bool = False,
) -> ReviewContinuation:
    normalized = str(verdict or "").strip().upper()
    if normalized not in REVIEW_VERDICTS:
        normalized = "NEEDS_OPERATOR"
        policy_reason = "unknown_review_verdict"
    if _normalize_head(expected_head_sha) != _normalize_head(observed_head_sha):
        normalized = "NEEDS_OPERATOR"
        policy_reason = "stale_or_moved_head"
    if protected and normalized == "APPROVE":
        normalized = "NEEDS_OPERATOR"
        policy_reason = "protected_merge_requires_operator"

    if normalized == "APPROVE":
        route_id = MERGE_ROUTE
        next_step = "authorized_merge_continuation"
    elif normalized == "REQUEST_CHANGES":
        route_id = REPAIR_ROUTE
        next_step = "bounded_repair_existing_pr_branch"
    elif normalized == "DO_NOT_MERGE":
        route_id = REPAIR_ROUTE
        next_step = "internal_repair_supersede_dependency"
    else:
        route_id = NEEDS_OPERATOR_ROUTE
        next_step = "operator_review_required"

    payload = _base_payload(
        repository=repository,
        pr_number=pr_number,
        head_sha=expected_head_sha,
        source_issue=source_issue,
        allowed_files=allowed_files,
        next_step=next_step,
    )
    payload.update(
        {
            "review_verdict": normalized,
            "observed_head_sha": _normalize_head(observed_head_sha),
            "permitted_merge_method": permitted_merge_method,
            "policy_reason": policy_reason,
            "protected": bool(protected),
            "operator_notification_allowed": normalized == "NEEDS_OPERATOR",
            "merge_executed": False,
        }
    )
    return _enqueue_runner_control(
        store,
        route_id=route_id,
        payload=payload,
        now=now,
    )


def durable_needs_operator_result(
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    permitted_merge_method: str,
    policy_reason: str,
    next_step: str,
) -> dict[str, Any]:
    return {
        "schema": "skeleton.runner_control_result.v1",
        "status": "NEEDS_OPERATOR",
        "repository": repository,
        "pr_number": pr_number,
        "head_sha": _normalize_head(head_sha),
        "permitted_merge_method": permitted_merge_method,
        "policy_reason": policy_reason,
        "next_step": next_step,
        "public_safe": True,
        "external_side_effects_executed": False,
    }


def _enqueue_runner_control(
    store: SchedulerStore,
    *,
    route_id: str,
    payload: Mapping[str, Any],
    now: int,
) -> ReviewContinuation:
    store.initialize()
    schedule = ScheduleSpec.from_mapping(
        {
            "schema": "skeleton.schedule.v1",
            "schedule_id": _schedule_id(route_id, payload),
            "trigger_kind": "once",
            "cron_expression": None,
            "once_at": now,
            "timezone": "UTC",
            "route_type": "runner",
            "route_id": route_id,
            "approval_policy": "auto_run_low_risk",
            "overlap_policy": "queue_one",
            "misfire_policy": "run_once",
            "payload": payload,
        }
    )
    stored, _ = store.register(schedule, now=now)
    occurrence_id = stable_occurrence_id(stored.spec.schedule_id, stored.version, now)
    proposal = build_execution_proposal(
        stored, occurrence_id=occurrence_id, scheduled_for=now
    )
    occurrence, created = store.create_occurrence(
        occurrence_id=occurrence_id,
        schedule=stored,
        scheduled_for=now,
        state="pending",
        reason="RUNNER_CONTROL_CONTINUATION_REQUIRED",
        proposal=proposal,
        now=now,
    )
    return ReviewContinuation(
        status=occurrence.state,
        schedule_id=stored.spec.schedule_id,
        occurrence_id=occurrence.occurrence_id,
        created=created,
        next_step=str(payload.get("next_step") or ""),
        payload=dict(payload),
    )


def _base_payload(
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    source_issue: int,
    allowed_files: Sequence[str],
    next_step: str,
) -> dict[str, Any]:
    return {
        "schema": "skeleton.internal_review_control.v1",
        "repository": repository,
        "pr_number": pr_number,
        "head_sha": _normalize_head(head_sha),
        "source_issue": source_issue,
        "allowed_files": sorted(str(path) for path in allowed_files),
        "next_step": next_step,
        "bounded": True,
        "privacy_boundary": "PUBLIC_SAFE_CODE_AND_SYNTHETIC_TESTS_ONLY",
        "approved_capabilities": ["repository_read", "repository_write", "test_execution"],
        "requested_capabilities": ["repository_read", "repository_write", "test_execution"],
        "public_safe": True,
        "external_side_effects_executed": False,
    }


def _schedule_id(route_id: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:24]
    return f"{route_id}.{digest}"


def _normalize_head(value: str) -> str:
    head = str(value or "").strip().lower()
    if _HEAD_SHA_RE.fullmatch(head) is None:
        raise ValueError("head SHA must be exactly 40 lowercase hex characters")
    return head
