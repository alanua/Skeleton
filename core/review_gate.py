from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from collections.abc import Sequence
from typing import Any, Final, Iterable, Mapping

from core.merge_policy_checker import (
    AUTO_MERGE_ALLOWED,
    NEVER_AUTO,
    OPERATOR_APPROVAL_REQUIRED,
    REVIEW_REQUIRED,
    DelegatedMergePolicyChecker,
)
from core.scheduler_models import (
    ScheduleSpec,
    build_execution_proposal,
    stable_occurrence_id,
)
from core.scheduler_store import SchedulerStore


APPROVE = "APPROVE"
REQUEST_CHANGES = "REQUEST_CHANGES"
DO_NOT_MERGE = "DO_NOT_MERGE"
NEEDS_OPERATOR = "NEEDS_OPERATOR"
VERDICTS = frozenset({APPROVE, REQUEST_CHANGES, DO_NOT_MERGE, NEEDS_OPERATOR})
REVIEW_GATE_SCHEMA = "skeleton.internal_review_gate.verdict.v1"
REPAIR_TASK_SCHEMA = "skeleton.internal_review_gate.repair_task.v1"
INTERNAL_REVIEW_ROUTE: Final = "runner.internal_review"
REPAIR_ROUTE: Final = "runner.internal_review_repair"
MERGE_ROUTE: Final = "runner.internal_review_merge"
NEEDS_OPERATOR_ROUTE: Final = "runner.needs_operator"
REVIEW_VERDICTS: Final = frozenset(
    {APPROVE, REQUEST_CHANGES, DO_NOT_MERGE, NEEDS_OPERATOR}
)

_HEAD_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,120}$")


@dataclass(frozen=True)
class ReviewGateRequest:
    repository: str
    pr_number: int
    expected_head_sha: str | None
    pr: Mapping[str, Any]
    changed_files: tuple[str, ...]
    validation_state: str
    validation_reason: str
    compare_status: str = "unknown"
    behind_by: int | None = None
    review_findings: tuple[str, ...] = ()
    scope_ok: bool = True
    privacy_ok: bool = True
    tests_ok: bool = True
    ci_ok: bool = True
    current_approvals: tuple[str, ...] = ()
    policy_triggers: tuple[str, ...] = ()
    risk_level: str = "green"


@dataclass(frozen=True)
class ReviewGateDecision:
    verdict: str
    reason: str
    receipt_id: str
    repair_task: Mapping[str, Any] | None = None
    operator_packet: Mapping[str, Any] | None = None
    continuation: str = "hold_internal"
    notify_operator: bool = False
    github_review_required: bool = False

    def to_receipt(self) -> dict[str, object]:
        receipt: dict[str, object] = {
            "schema": REVIEW_GATE_SCHEMA,
            "verdict": self.verdict,
            "reason": self.reason,
            "receipt_id": self.receipt_id,
            "continuation": self.continuation,
            "notify_operator": self.notify_operator,
            "github_review_required": self.github_review_required,
        }
        if self.repair_task is not None:
            receipt["repair_task"] = dict(self.repair_task)
        if self.operator_packet is not None:
            receipt["operator_packet"] = dict(self.operator_packet)
        return receipt


@dataclass(frozen=True)
class ReviewContinuation:
    status: str
    schedule_id: str
    occurrence_id: str
    created: bool
    next_step: str
    payload: Mapping[str, Any]


def evaluate_review_gate(
    request: ReviewGateRequest,
    *,
    prior_receipts: Iterable[Mapping[str, Any] | str] = (),
    policy_checker: DelegatedMergePolicyChecker | None = None,
) -> ReviewGateDecision:
    """Evaluate one freshly-read PR state and return a durable internal verdict."""

    head_sha = _head_sha(request.pr)
    receipt_id = _receipt_id(request, head_sha)
    stale_reason = _stale_or_unmergeable_reason(request, head_sha)
    if stale_reason is not None:
        return ReviewGateDecision(
            verdict=DO_NOT_MERGE,
            reason=stale_reason,
            receipt_id=receipt_id,
            continuation="repair_or_supersede",
        )

    if not request.scope_ok:
        return ReviewGateDecision(DO_NOT_MERGE, "scope_check_failed", receipt_id)
    if not request.privacy_ok:
        return ReviewGateDecision(
            NEEDS_OPERATOR,
            "security_or_secret_boundary",
            receipt_id,
            continuation="escalate_to_operator",
            notify_operator=True,
        )
    if not request.tests_ok:
        return _request_changes_decision(request, receipt_id, prior_receipts, "tests_not_green")
    if not request.ci_ok or request.validation_state != "success":
        return _request_changes_decision(
            request,
            receipt_id,
            prior_receipts,
            request.validation_reason or "validation_not_success",
        )
    if request.review_findings:
        return _request_changes_decision(
            request,
            receipt_id,
            prior_receipts,
            "review_findings_present",
        )

    policy = (policy_checker or DelegatedMergePolicyChecker()).check(
        {
            "changed_files": request.changed_files,
            "clean_pr": True,
            "evidence": {
                "tests_passed": request.tests_ok,
                "diff_check_passed": True,
                "review_context_present": True,
            },
            "risk_level": request.risk_level,
            "triggers": request.policy_triggers,
        }
    )
    if policy.verdict == OPERATOR_APPROVAL_REQUIRED:
        reason = _first_reason(policy.reasons, "operator_authority_required")
        return ReviewGateDecision(
            NEEDS_OPERATOR,
            reason,
            receipt_id,
            operator_packet=_operator_packet(request, head_sha, reason),
            continuation="escalate_to_operator",
            notify_operator=True,
        )
    if policy.verdict == NEVER_AUTO:
        return ReviewGateDecision(
            DO_NOT_MERGE,
            _first_reason(policy.reasons, "policy_never_auto"),
            receipt_id,
            continuation="repair_or_supersede",
        )
    if policy.verdict == REVIEW_REQUIRED:
        return _request_changes_decision(
            request,
            receipt_id,
            prior_receipts,
            _first_reason(policy.reasons, "policy_review_required"),
        )
    if policy.verdict != AUTO_MERGE_ALLOWED:
        return ReviewGateDecision(DO_NOT_MERGE, "policy_unknown", receipt_id)

    return ReviewGateDecision(
        verdict=APPROVE,
        reason="all_internal_checks_passed",
        receipt_id=receipt_id,
        continuation="continue_authorized_workflow",
    )


def render_review_gate_report(decision: ReviewGateDecision) -> str:
    receipt = decision.to_receipt()
    lines = [
        "Internal review gate receipt",
        f"schema={receipt['schema']}",
        f"internal_review_verdict={decision.verdict}",
        f"reason={decision.reason}",
        f"receipt_id={decision.receipt_id}",
        f"continuation={decision.continuation}",
        f"notify_operator={str(decision.notify_operator).lower()}",
        f"github_review_required={str(decision.github_review_required).lower()}",
    ]
    repair_task = decision.repair_task
    if repair_task is not None:
        lines.extend(
            (
                f"repair_task_id={repair_task['task_id']}",
                f"repair_idempotency_key={repair_task['idempotency_key']}",
                f"repair_reused_existing={str(repair_task['reused_existing']).lower()}",
            )
        )
    operator_packet = decision.operator_packet
    if operator_packet is not None:
        lines.extend(
            (
                f"operator_repository={operator_packet['repository']}",
                f"operator_pr_number={operator_packet['pr_number']}",
                f"operator_head_sha={operator_packet['head_sha']}",
                f"operator_permitted_merge_method={operator_packet['permitted_merge_method']}",
                f"operator_policy_reason={operator_packet['policy_reason']}",
                f"operator_next_continuation_step={operator_packet['next_continuation_step']}",
            )
        )
    return "\n".join(lines)


def parse_review_gate_receipts(
    comments: Iterable[Mapping[str, Any] | str],
) -> tuple[dict[str, str], ...]:
    receipts: list[dict[str, str]] = []
    for comment in comments:
        body = comment if isinstance(comment, str) else comment.get("body")
        if not isinstance(body, str) or "internal_review_verdict=" not in body:
            continue
        fields: dict[str, str] = {}
        for line in body.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key in {
                "internal_review_verdict",
                "receipt_id",
                "repair_task_id",
                "repair_idempotency_key",
            }:
                fields[key] = value
        if fields:
            receipts.append(fields)
    return tuple(receipts)


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
        normalized = NEEDS_OPERATOR
        policy_reason = "unknown_review_verdict"
    if _normalize_head(expected_head_sha) != _normalize_head(observed_head_sha):
        normalized = NEEDS_OPERATOR
        policy_reason = "stale_or_moved_head"
    if protected and normalized == APPROVE:
        normalized = NEEDS_OPERATOR
        policy_reason = "protected_merge_requires_operator"

    if normalized == APPROVE:
        route_id = MERGE_ROUTE
        next_step = "authorized_merge_continuation"
    elif normalized == REQUEST_CHANGES:
        route_id = REPAIR_ROUTE
        next_step = "bounded_repair_existing_pr_branch"
    elif normalized == DO_NOT_MERGE:
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
            "operator_notification_allowed": normalized == NEEDS_OPERATOR,
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
        "status": NEEDS_OPERATOR,
        "repository": repository,
        "pr_number": pr_number,
        "head_sha": _normalize_head(head_sha),
        "permitted_merge_method": permitted_merge_method,
        "policy_reason": policy_reason,
        "next_step": next_step,
        "public_safe": True,
        "external_side_effects_executed": False,
    }


def _request_changes_decision(
    request: ReviewGateRequest,
    receipt_id: str,
    prior_receipts: Iterable[Mapping[str, Any] | str],
    reason: str,
) -> ReviewGateDecision:
    repair_task = _repair_task(request, reason, prior_receipts)
    return ReviewGateDecision(
        verdict=REQUEST_CHANGES,
        reason=reason,
        receipt_id=receipt_id,
        repair_task=repair_task,
        continuation="activate_bounded_repair",
    )


def _repair_task(
    request: ReviewGateRequest,
    reason: str,
    prior_receipts: Iterable[Mapping[str, Any] | str],
) -> dict[str, object]:
    key = _repair_idempotency_key(request, reason)
    existing = _existing_repair_task_id(key, prior_receipts)
    task_id = existing or f"repair-pr-{request.pr_number}-{key[-12:]}"
    return {
        "schema": REPAIR_TASK_SCHEMA,
        "task_id": task_id,
        "idempotency_key": key,
        "repository": request.repository,
        "pr_number": request.pr_number,
        "head_sha": _head_sha(request.pr),
        "base_branch": str((request.pr.get("base") or {}).get("ref") or ""),
        "head_branch": str((request.pr.get("head") or {}).get("ref") or ""),
        "changed_files": request.changed_files,
        "reason": reason,
        "reused_existing": existing is not None,
        "uses_existing_pr": True,
        "public_safe": True,
    }


def _operator_packet(
    request: ReviewGateRequest, head_sha: str, reason: str
) -> dict[str, object]:
    return {
        "schema": "skeleton.internal_review_gate.operator_packet.v1",
        "repository": request.repository,
        "pr_number": request.pr_number,
        "head_sha": head_sha,
        "permitted_merge_method": "squash",
        "policy_reason": reason,
        "next_continuation_step": "runtime_sync_main",
        "post_merge_continuation": (
            "runtime_sync_main pinned to merged main SHA, then registered live-safe "
            "canary, then resume previously blocked approved work"
        ),
        "public_safe": True,
        "external_side_effects_executed": False,
    }


def _existing_repair_task_id(
    idempotency_key: str, prior_receipts: Iterable[Mapping[str, Any] | str]
) -> str | None:
    for receipt in parse_review_gate_receipts(prior_receipts):
        if receipt.get("repair_idempotency_key") != idempotency_key:
            continue
        task_id = receipt.get("repair_task_id")
        if task_id and _SAFE_TOKEN_RE.fullmatch(task_id):
            return task_id
    return None


def _repair_idempotency_key(request: ReviewGateRequest, reason: str) -> str:
    payload = {
        "repo": request.repository,
        "pr": request.pr_number,
        "head": _head_sha(request.pr),
        "reason": reason,
        "findings": tuple(sorted(request.review_findings)),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:24]


def _receipt_id(request: ReviewGateRequest, head_sha: str) -> str:
    payload = {
        "repo": request.repository,
        "pr": request.pr_number,
        "expected": request.expected_head_sha,
        "head": head_sha,
        "files": request.changed_files,
        "validation": request.validation_state,
        "findings": tuple(sorted(request.review_findings)),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:24]


def _head_sha(pr: Mapping[str, Any]) -> str:
    return str((pr.get("head") or {}).get("sha") or "").lower()


def _stale_or_unmergeable_reason(
    request: ReviewGateRequest, head_sha: str
) -> str | None:
    if str(request.pr.get("state") or "").lower() != "open":
        return "pr_not_open"
    if request.expected_head_sha is not None and head_sha != request.expected_head_sha:
        return "expected_head_sha_mismatch"
    if not _HEAD_SHA_RE.fullmatch(head_sha):
        return "head_sha_unavailable"
    if request.compare_status in {"behind", "diverged"}:
        return "branch_behind_or_diverged"
    if isinstance(request.behind_by, int) and request.behind_by > 0:
        return "branch_behind_or_diverged"
    mergeable = request.pr.get("mergeable")
    mergeable_state = str(request.pr.get("mergeable_state") or "").lower()
    if mergeable is False or mergeable_state in {"dirty", "blocked"}:
        return "pr_has_merge_conflicts"
    return None


def _first_reason(reasons: tuple[str, ...], fallback: str) -> str:
    return reasons[0] if reasons else fallback


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
