from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Final

from core.merge_policy_checker import (
    AUTO_MERGE_ALLOWED,
    NEVER_AUTO,
    OPERATOR_APPROVAL_REQUIRED,
    REVIEW_REQUIRED,
    DelegatedMergePolicyChecker,
)
from core.notification_policy import claim_operator_notification
from core.scheduler_models import ScheduleSpec, build_execution_proposal, stable_occurrence_id
from core.scheduler_store import SchedulerStore


APPROVE: Final = "APPROVE"
REQUEST_CHANGES: Final = "REQUEST_CHANGES"
DO_NOT_MERGE: Final = "DO_NOT_MERGE"
NEEDS_OPERATOR: Final = "NEEDS_OPERATOR"
REVIEW_VERDICTS: Final = frozenset({APPROVE, REQUEST_CHANGES, DO_NOT_MERGE, NEEDS_OPERATOR})
REVIEW_GATE_SCHEMA: Final = "skeleton.internal_review_gate.verdict.v1"
REPAIR_TASK_SCHEMA: Final = "skeleton.internal_review_gate.repair_task.v1"
INTERNAL_REVIEW_CONTROL_ROUTE: Final = "internal_review_control"
INTERNAL_REVIEW_SCHEMA: Final = "skeleton.internal_review_control.v1"
PRIVACY_PUBLIC_SAFE_CODE: Final = "PUBLIC_SAFE_CODE_AND_SYNTHETIC_TESTS_ONLY"
REPAIR_EVENT_KIND: Final = "internal_review_repair_task.v1"
APPROVAL_EVENT_KIND: Final = "internal_review_authorized_continuation.v1"
REREVIEW_EVENT_KIND: Final = "internal_review_repair_done.v1"

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


ReviewStateReader = Callable[[Mapping[str, Any]], Mapping[str, Any]]
RepairEnqueueAdapter = Callable[[Mapping[str, Any]], Mapping[str, Any]]
AuthorizedContinuationAdapter = Callable[[Mapping[str, Any]], Mapping[str, Any]]
NeedsOperatorDeliveryAdapter = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class ReviewControlAdapters:
    state_reader: ReviewStateReader
    repair_enqueue: RepairEnqueueAdapter | None = None
    authorized_continuation: AuthorizedContinuationAdapter | None = None
    needs_operator_delivery: NeedsOperatorDeliveryAdapter | None = None


def evaluate_review_gate(
    request: ReviewGateRequest,
    *,
    prior_receipts: Iterable[Mapping[str, Any] | str] = (),
    policy_checker: DelegatedMergePolicyChecker | None = None,
) -> ReviewGateDecision:
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
        return _needs_operator_decision(request, receipt_id, head_sha, "security_or_secret_boundary")
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
        return _needs_operator_decision(
            request,
            receipt_id,
            head_sha,
            _first_reason(policy.reasons, "operator_authority_required"),
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
    return enqueue_internal_review_control(store, payload=payload, now=now)


def dispatch_internal_review_control(
    payload: Mapping[str, Any],
    *,
    store: SchedulerStore,
    now: int,
    adapters: ReviewControlAdapters,
) -> dict[str, Any]:
    control = _validated_control_payload(payload)
    next_step = str(control.get("next_step") or "")
    if next_step == "internal_review":
        return _dispatch_internal_review(control, store=store, now=now, adapters=adapters)
    if next_step == "bounded_repair_existing_pr_branch":
        return _dispatch_repair(control, store=store, now=now, adapter=adapters.repair_enqueue)
    if next_step == "repair_done":
        return _dispatch_repair_done(control, store=store, now=now)
    if next_step == "authorized_merge_continuation":
        return _dispatch_authorized_continuation(
            control,
            store=store,
            now=now,
            adapter=adapters.authorized_continuation,
        )
    if next_step == "operator_review_required":
        return _dispatch_needs_operator(
            control,
            store=store,
            now=now,
            adapter=adapters.needs_operator_delivery,
        )
    if next_step == "internal_repair_supersede_dependency":
        return _terminal_control_receipt(control, next_step=next_step)
    return _blocked_receipt("UNKNOWN_INTERNAL_REVIEW_STEP")


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
    if normalized == APPROVE and _normalize_head(expected_head_sha) != _normalize_head(observed_head_sha):
        normalized = NEEDS_OPERATOR
        policy_reason = "stale_or_moved_head"
    if protected and normalized == APPROVE:
        normalized = NEEDS_OPERATOR
        policy_reason = "protected_merge_requires_operator"

    if normalized == APPROVE:
        next_step = "authorized_merge_continuation"
    elif normalized == REQUEST_CHANGES:
        next_step = "bounded_repair_existing_pr_branch"
    elif normalized == DO_NOT_MERGE:
        next_step = "internal_repair_supersede_dependency"
    else:
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
    return enqueue_internal_review_control(store, payload=payload, now=now)


def schedule_verified_repair_done(
    store: SchedulerStore,
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    source_issue: int,
    allowed_files: Sequence[str],
    repair_task_id: str,
    repair_idempotency_key: str,
    publication_status: str,
    now: int,
) -> ReviewContinuation:
    payload = _base_payload(
        repository=repository,
        pr_number=pr_number,
        head_sha=head_sha,
        source_issue=source_issue,
        allowed_files=allowed_files,
        next_step="repair_done",
    )
    payload.update(
        {
            "repair_task_id": repair_task_id,
            "repair_idempotency_key": repair_idempotency_key,
            "publication_status": publication_status,
        }
    )
    return enqueue_internal_review_control(store, payload=payload, now=now)


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
    if decision.repair_task is not None:
        lines.extend(
            (
                f"repair_task_id={decision.repair_task['task_id']}",
                f"repair_idempotency_key={decision.repair_task['idempotency_key']}",
                f"repair_reused_existing={str(decision.repair_task['reused_existing']).lower()}",
            )
        )
    if decision.operator_packet is not None:
        packet = decision.operator_packet
        lines.extend(
            (
                f"operator_repository={packet['repository']}",
                f"operator_pr_number={packet['pr_number']}",
                f"operator_head_sha={packet['head_sha']}",
                f"operator_permitted_merge_method={packet['permitted_merge_method']}",
                f"operator_policy_reason={packet['policy_reason']}",
                f"operator_next_continuation_step={packet['next_continuation_step']}",
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
            if key.strip() in {
                "internal_review_verdict",
                "receipt_id",
                "repair_task_id",
                "repair_idempotency_key",
            }:
                fields[key.strip()] = value.strip()
        if fields:
            receipts.append(fields)
    return tuple(receipts)


def repair_task_ledger_key(
    *, repository: str, pr_number: int, head_sha: str, idempotency_key: str
) -> str:
    return "repair:" + _digest(
        {
            "repository": repository,
            "pr_number": pr_number,
            "head_sha": _normalize_head(head_sha),
            "idempotency_key": idempotency_key,
        }
    )[:40]


def control_action_ledger_key(
    *, action: str, repository: str, pr_number: int, head_sha: str, reason: str
) -> str:
    return "control:" + _digest(
        {
            "action": action,
            "repository": repository,
            "pr_number": pr_number,
            "head_sha": _normalize_head(head_sha),
            "reason": reason,
        }
    )[:40]


def enqueue_internal_review_control(
    store: SchedulerStore,
    *,
    payload: Mapping[str, Any],
    now: int,
) -> ReviewContinuation:
    store.initialize()
    route_id = INTERNAL_REVIEW_CONTROL_ROUTE
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
    proposal = build_execution_proposal(stored, occurrence_id=occurrence_id, scheduled_for=now)
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


def _dispatch_internal_review(
    control: Mapping[str, Any],
    *,
    store: SchedulerStore,
    now: int,
    adapters: ReviewControlAdapters,
) -> dict[str, Any]:
    state = adapters.state_reader(control)
    pr = _mapping(state.get("pr"))
    files = _sequence_of_mappings(state.get("files"))
    compare = _mapping(state.get("compare"))
    combined_status = _mapping(state.get("combined_status"))
    check_runs = _sequence_of_mappings(state.get("check_runs"))
    validation_state, validation_reason = validation_summary(combined_status, check_runs)
    changed_files = tuple(
        str(file.get("filename")) for file in files if isinstance(file.get("filename"), str)
    )
    review_findings = _string_tuple(state.get("review_findings"))
    request = ReviewGateRequest(
        repository=str(control["repository"]),
        pr_number=int(control["pr_number"]),
        expected_head_sha=str(control["head_sha"]),
        pr=pr,
        changed_files=changed_files,
        validation_state=validation_state,
        validation_reason=validation_reason,
        compare_status=str(compare.get("status") or "unknown").lower(),
        behind_by=compare.get("behind_by") if isinstance(compare.get("behind_by"), int) else None,
        review_findings=review_findings,
        scope_ok=_bool(state.get("scope_ok"), True),
        privacy_ok=_bool(state.get("privacy_ok"), True),
        tests_ok=_bool(state.get("tests_ok"), validation_state == "success"),
        ci_ok=_bool(state.get("ci_ok"), validation_state == "success"),
        policy_triggers=_string_tuple(state.get("policy_triggers")),
        risk_level=str(state.get("risk_level") or "green"),
    )
    decision = evaluate_review_gate(request, prior_receipts=_prior_receipts(store, control))
    observed_head = _head_sha(pr)
    continuation = continue_from_internal_review_verdict(
        store,
        verdict=decision.verdict,
        repository=request.repository,
        pr_number=request.pr_number,
        expected_head_sha=str(control["head_sha"]),
        observed_head_sha=observed_head,
        permitted_merge_method="squash",
        policy_reason=decision.reason,
        source_issue=int(control["source_issue"]),
        allowed_files=tuple(str(path) for path in control["allowed_files"]),
        now=now,
    )
    receipt = shared_continuation_receipt(decision)
    receipt.update(
        {
            "status": "DONE",
            "accepted": True,
            "decision": "ACCEPT",
            "reason": decision.reason,
            "materialized_continuation": {
                "schedule_id": continuation.schedule_id,
                "occurrence_id": continuation.occurrence_id,
                "created": continuation.created,
                "next_step": continuation.next_step,
                "payload": dict(continuation.payload),
            },
            "public_safe": True,
            "external_side_effects_executed": False,
        }
    )
    return receipt


def _dispatch_repair(
    control: Mapping[str, Any],
    *,
    store: SchedulerStore,
    now: int,
    adapter: RepairEnqueueAdapter | None,
) -> dict[str, Any]:
    if adapter is None:
        return _blocked_receipt("ACTION_AUTHORITY_UNAVAILABLE")
    repair_task = _repair_task_from_control(control)
    ledger_key = repair_task_ledger_key(
        repository=str(control["repository"]),
        pr_number=int(control["pr_number"]),
        head_sha=str(control["head_sha"]),
        idempotency_key=str(repair_task["idempotency_key"]),
    )
    existing = store.get_operational_event(ledger_key)
    if existing is None:
        adapter_receipt = adapter(repair_task)
        if not _adapter_done(adapter_receipt):
            return _blocked_receipt("REPAIR_ENQUEUE_REJECTED")
        event, created = store.record_operational_event_once(
            ledger_key=ledger_key,
            event_kind=REPAIR_EVENT_KIND,
            payload={
                "schema": "skeleton.internal_review_gate.repair_enqueued.v1",
                "repair_task": dict(repair_task),
                "adapter_receipt": dict(adapter_receipt),
                "public_safe": True,
                "external_side_effects_executed": False,
            },
            now=now,
        )
    else:
        event = existing
        created = False
    return {
        "schema": "skeleton.shared_dispatch.review_continuation.v1",
        "status": "DONE",
        "accepted": True,
        "decision": "ACCEPT",
        "reason": "repair_task_enqueued" if created else "repair_task_reused",
        "internal_review_verdict": REQUEST_CHANGES,
        "continuation": "await_repair_done",
        "repair_task": dict(repair_task),
        "repair_event": event,
        "repair_enqueued": created,
        "re_review_scheduled": False,
        "public_safe": True,
        "external_side_effects_executed": False,
    }


def _dispatch_repair_done(
    control: Mapping[str, Any],
    *,
    store: SchedulerStore,
    now: int,
) -> dict[str, Any]:
    if control.get("publication_status") != "DONE":
        return _blocked_receipt("REPAIR_DONE_NOT_VERIFIED")
    ledger_key = control_action_ledger_key(
        action="repair_done",
        repository=str(control["repository"]),
        pr_number=int(control["pr_number"]),
        head_sha=str(control["head_sha"]),
        reason=str(control.get("repair_idempotency_key") or ""),
    )
    existing = store.get_operational_event(ledger_key)
    created = False
    if existing is None:
        _, created = store.record_operational_event_once(
            ledger_key=ledger_key,
            event_kind=REREVIEW_EVENT_KIND,
            payload={
                "schema": "skeleton.internal_review_gate.repair_done.v1",
                "repair_task_id": control.get("repair_task_id"),
                "repair_idempotency_key": control.get("repair_idempotency_key"),
                "publication_status": "DONE",
                "public_safe": True,
                "external_side_effects_executed": False,
            },
            now=now,
        )
    rereview = _base_payload(
        repository=str(control["repository"]),
        pr_number=int(control["pr_number"]),
        head_sha=str(control["head_sha"]),
        source_issue=int(control["source_issue"]),
        allowed_files=tuple(str(path) for path in control["allowed_files"]),
        next_step="internal_review",
    )
    rereview["repair_parent_reason"] = str(control.get("repair_idempotency_key") or "repair_done")
    continuation = enqueue_internal_review_control(store, payload=rereview, now=now + 1)
    return {
        "schema": "skeleton.shared_dispatch.review_continuation.v1",
        "status": "DONE",
        "accepted": True,
        "decision": "ACCEPT",
        "reason": "repair_done_verified",
        "continuation": "schedule_re_review",
        "repair_done_recorded": created,
        "materialized_continuation": {
            "schedule_id": continuation.schedule_id,
            "occurrence_id": continuation.occurrence_id,
            "created": continuation.created,
            "next_step": continuation.next_step,
        },
        "public_safe": True,
        "external_side_effects_executed": False,
    }


def _dispatch_authorized_continuation(
    control: Mapping[str, Any],
    *,
    store: SchedulerStore,
    now: int,
    adapter: AuthorizedContinuationAdapter | None,
) -> dict[str, Any]:
    if adapter is None:
        return _blocked_receipt("ACTION_AUTHORITY_UNAVAILABLE")
    packet = _control_packet(control, action="authorized_merge_continuation")
    ledger_key = control_action_ledger_key(
        action="authorized_merge_continuation",
        repository=str(control["repository"]),
        pr_number=int(control["pr_number"]),
        head_sha=str(control["head_sha"]),
        reason=str(control.get("policy_reason") or ""),
    )
    existing = store.get_operational_event(ledger_key)
    created = False
    if existing is None:
        adapter_receipt = adapter(packet)
        if not _adapter_done(adapter_receipt):
            return _blocked_receipt("AUTHORIZED_CONTINUATION_REJECTED")
        event, created = store.record_operational_event_once(
            ledger_key=ledger_key,
            event_kind=APPROVAL_EVENT_KIND,
            payload={**packet, "adapter_receipt": dict(adapter_receipt)},
            now=now,
        )
    else:
        event = existing
    receipt = _terminal_control_receipt(control, next_step="authorized_merge_continuation")
    receipt.update({"authorized_continuation": event, "authorized_continuation_called": created})
    return receipt


def _dispatch_needs_operator(
    control: Mapping[str, Any],
    *,
    store: SchedulerStore,
    now: int,
    adapter: NeedsOperatorDeliveryAdapter | None,
) -> dict[str, Any]:
    if adapter is None:
        return _blocked_receipt("ACTION_AUTHORITY_UNAVAILABLE")
    packet = durable_needs_operator_result(
        repository=str(control["repository"]),
        pr_number=int(control["pr_number"]),
        head_sha=str(control["head_sha"]),
        permitted_merge_method=str(control.get("permitted_merge_method") or "squash"),
        policy_reason=str(control.get("policy_reason") or "operator_review_required"),
        next_step=str(control.get("next_step") or "operator_review_required"),
    )
    claimed = claim_operator_notification(
        store,
        status=NEEDS_OPERATOR,
        now=now,
        repository=str(control["repository"]),
        pr_number=int(control["pr_number"]),
        head_sha=str(control["head_sha"]),
        reason=str(control.get("policy_reason") or "operator_review_required"),
        payload=packet,
    )
    adapter_receipt: Mapping[str, Any] = {"status": "DONE", "reused_existing": True}
    if claimed:
        adapter_receipt = adapter(packet)
        if not _adapter_done(adapter_receipt):
            return _blocked_receipt("NEEDS_OPERATOR_DELIVERY_REJECTED")
    receipt = _terminal_control_receipt(control, next_step="operator_review_required")
    receipt.update(
        {
            "operator_packet": packet,
            "operator_notification_claimed": claimed,
            "delivery_receipt": dict(adapter_receipt),
        }
    )
    return receipt


def _terminal_control_receipt(control: Mapping[str, Any], *, next_step: str) -> dict[str, Any]:
    status = NEEDS_OPERATOR if next_step == "operator_review_required" else "DONE"
    return {
        "schema": "skeleton.shared_dispatch.review_continuation.v1",
        "status": status,
        "accepted": True,
        "decision": "REVIEW" if status == NEEDS_OPERATOR else "ACCEPT",
        "reason": str(control.get("policy_reason") or next_step),
        "next_step": next_step,
        "repository": control["repository"],
        "pr_number": control["pr_number"],
        "head_sha": control["head_sha"],
        "public_safe": True,
        "external_side_effects_executed": False,
    }


def shared_continuation_receipt(decision: ReviewGateDecision) -> dict[str, object]:
    loop_event = {
        APPROVE: "STEP_SUCCEEDED",
        REQUEST_CHANGES: "STEP_FAILED",
        NEEDS_OPERATOR: "OPERATOR_REQUIRED",
        DO_NOT_MERGE: "REVIEW_REQUIRED",
    }[decision.verdict]
    receipt: dict[str, object] = {
        "schema": "skeleton.shared_dispatch.review_continuation.v1",
        "internal_review_verdict": decision.verdict,
        "loop_event": loop_event,
        "continuation": decision.continuation,
        "notify_operator": decision.notify_operator,
        "receipt_id": decision.receipt_id,
    }
    if decision.operator_packet is not None:
        receipt["operator_packet"] = dict(decision.operator_packet)
    if decision.repair_task is not None:
        receipt["repair_task"] = dict(decision.repair_task)
    return receipt


def validation_summary(
    combined_status: Mapping[str, Any], check_runs: Sequence[Mapping[str, Any]]
) -> tuple[str, str]:
    statuses = combined_status.get("statuses")
    status_items = statuses if isinstance(statuses, list) else []
    status_state = str(combined_status.get("state") or "").lower()
    if not status_items and not check_runs:
        return "missing", "validation_missing"
    check_states = {str(check.get("status") or "").lower() for check in check_runs}
    check_conclusions = {
        str(check.get("conclusion") or "").lower()
        for check in check_runs
        if check.get("conclusion") is not None
    }
    checks_success = not check_runs or (
        check_states <= {"completed"} and check_conclusions <= {"success", "neutral", "skipped"}
    )
    statuses_success = not status_items or status_state == "success"
    if statuses_success and checks_success:
        return "success", "none"
    return "not_success", "validation_not_success"


def _request_changes_decision(
    request: ReviewGateRequest,
    receipt_id: str,
    prior_receipts: Iterable[Mapping[str, Any] | str],
    reason: str,
) -> ReviewGateDecision:
    return ReviewGateDecision(
        verdict=REQUEST_CHANGES,
        reason=reason,
        receipt_id=receipt_id,
        repair_task=_repair_task(request, reason, prior_receipts),
        continuation="activate_bounded_repair",
    )


def _needs_operator_decision(
    request: ReviewGateRequest, receipt_id: str, head_sha: str, reason: str
) -> ReviewGateDecision:
    return ReviewGateDecision(
        NEEDS_OPERATOR,
        reason,
        receipt_id,
        operator_packet=_operator_packet(request, head_sha, reason),
        continuation="escalate_to_operator",
        notify_operator=True,
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
        "base_branch": str((_mapping(request.pr.get("base"))).get("ref") or ""),
        "head_branch": str((_mapping(request.pr.get("head"))).get("ref") or ""),
        "changed_files": request.changed_files,
        "allowed_files": tuple(sorted(request.changed_files)),
        "reason": reason,
        "reused_existing": existing is not None,
        "uses_existing_pr": True,
        "public_safe": True,
    }


def _repair_task_from_control(control: Mapping[str, Any]) -> dict[str, object]:
    key = _digest(
        {
            "repo": control["repository"],
            "pr": control["pr_number"],
            "head": _normalize_head(str(control["head_sha"])),
            "reason": control.get("policy_reason"),
            "allowed_files": tuple(str(path) for path in control["allowed_files"]),
        }
    )[:24]
    return {
        "schema": REPAIR_TASK_SCHEMA,
        "task_id": f"repair-pr-{control['pr_number']}-{key[-12:]}",
        "idempotency_key": key,
        "repository": control["repository"],
        "pr_number": control["pr_number"],
        "head_sha": _normalize_head(str(control["head_sha"])),
        "allowed_files": tuple(str(path) for path in control["allowed_files"]),
        "reason": str(control.get("policy_reason") or "request_changes"),
        "public_safe": True,
        "external_side_effects_executed": False,
    }


def _operator_packet(request: ReviewGateRequest, head_sha: str, reason: str) -> dict[str, object]:
    return {
        "schema": "skeleton.internal_review_gate.operator_packet.v1",
        "repository": request.repository,
        "pr_number": request.pr_number,
        "head_sha": head_sha,
        "permitted_merge_method": "squash",
        "policy_reason": reason,
        "next_continuation_step": "runtime_sync_main",
        "public_safe": True,
        "external_side_effects_executed": False,
    }


def _control_packet(control: Mapping[str, Any], *, action: str) -> dict[str, Any]:
    return {
        "schema": "skeleton.internal_review_gate.control_action.v1",
        "action": action,
        "repository": control["repository"],
        "pr_number": control["pr_number"],
        "head_sha": control["head_sha"],
        "permitted_merge_method": control.get("permitted_merge_method", "squash"),
        "policy_reason": control.get("policy_reason"),
        "allowed_files": tuple(str(path) for path in control["allowed_files"]),
        "public_safe": True,
        "external_side_effects_executed": False,
    }


def _validated_control_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("INVALID_INTERNAL_REVIEW_PAYLOAD")
    if payload.get("schema") != INTERNAL_REVIEW_SCHEMA:
        raise ValueError("INVALID_INTERNAL_REVIEW_SCHEMA")
    if payload.get("privacy_boundary") != PRIVACY_PUBLIC_SAFE_CODE:
        raise ValueError("PRIVACY_BOUNDARY_MISMATCH")
    if payload.get("bounded") is not True:
        raise ValueError("UNBOUNDED_INTERNAL_REVIEW_PAYLOAD")
    _normalize_head(str(payload.get("head_sha") or ""))
    if not isinstance(payload.get("repository"), str) or not payload.get("repository"):
        raise ValueError("INVALID_REPOSITORY")
    if not isinstance(payload.get("pr_number"), int) or payload.get("pr_number") <= 0:
        raise ValueError("INVALID_PR_NUMBER")
    if not isinstance(payload.get("source_issue"), int) or payload.get("source_issue") <= 0:
        raise ValueError("INVALID_SOURCE_ISSUE")
    allowed_files = payload.get("allowed_files")
    if (
        not isinstance(allowed_files, list)
        or not allowed_files
        or any(not isinstance(path, str) or not path for path in allowed_files)
    ):
        raise ValueError("INVALID_ALLOWED_FILES")
    return payload


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
        "schema": INTERNAL_REVIEW_SCHEMA,
        "repository": repository,
        "pr_number": pr_number,
        "head_sha": _normalize_head(head_sha),
        "source_issue": source_issue,
        "allowed_files": sorted(str(path) for path in allowed_files),
        "next_step": next_step,
        "bounded": True,
        "privacy_boundary": PRIVACY_PUBLIC_SAFE_CODE,
        "requested_capabilities": ["repository_read"],
        "public_safe": True,
        "external_side_effects_executed": False,
    }


def _prior_receipts(store: SchedulerStore, control: Mapping[str, Any]) -> tuple[str, ...]:
    schedule_id = _schedule_id(INTERNAL_REVIEW_CONTROL_ROUTE, control)
    return tuple(
        json.dumps(receipt["result"], sort_keys=True)
        for occurrence in store.list_occurrences(schedule_id)
        for receipt in store.list_dispatch_receipts(occurrence.occurrence_id)
    )


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
    return _digest(payload)[:24]


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
    return _digest(payload)[:24]


def _schedule_id(route_id: str, payload: Mapping[str, Any]) -> str:
    return f"{route_id}.{_digest(dict(payload))[:24]}"


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _head_sha(pr: Mapping[str, Any]) -> str:
    return str((_mapping(pr.get("head"))).get("sha") or "").lower()


def _stale_or_unmergeable_reason(request: ReviewGateRequest, head_sha: str) -> str | None:
    if str(request.pr.get("state") or "").lower() != "open":
        return "pr_not_open"
    if request.expected_head_sha is not None and head_sha != request.expected_head_sha:
        return "expected_head_sha_mismatch"
    if _HEAD_SHA_RE.fullmatch(head_sha) is None:
        return "head_sha_unavailable"
    if request.compare_status in {"behind", "diverged"}:
        return "branch_behind_or_diverged"
    if isinstance(request.behind_by, int) and request.behind_by > 0:
        return "branch_behind_or_diverged"
    mergeable_state = str(request.pr.get("mergeable_state") or "").lower()
    if request.pr.get("mergeable") is False or mergeable_state in {"dirty", "blocked"}:
        return "pr_has_merge_conflicts"
    return None


def _normalize_head(value: str) -> str:
    head = str(value or "").strip().lower()
    if _HEAD_SHA_RE.fullmatch(head) is None:
        raise ValueError("head SHA must be exactly 40 lowercase hex characters")
    return head


def _first_reason(reasons: tuple[str, ...], fallback: str) -> str:
    return reasons[0] if reasons else fallback


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence_of_mappings(value: object) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)


def _bool(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _adapter_done(receipt: Mapping[str, Any]) -> bool:
    return isinstance(receipt, Mapping) and str(receipt.get("status") or "") == "DONE"


def _blocked_receipt(reason: str) -> dict[str, Any]:
    return {
        "schema": "skeleton.shared_dispatch.review_continuation.v1",
        "status": "BLOCKED",
        "accepted": False,
        "decision": "REJECT",
        "reason": reason,
        "public_safe": True,
        "external_side_effects_executed": False,
    }
