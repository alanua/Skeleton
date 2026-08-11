from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import time
from typing import Any

from core.scheduler_models import (
    TICK_RECEIPT_SCHEMA,
    ScheduleSpec,
    StoredSchedule,
    build_execution_proposal,
    iter_due_times,
    stable_followup_occurrence_id,
    stable_occurrence_id,
)
from core.scheduler_store import SchedulerStore
from core.control_recovery import CONTROL_RECOVERY_SCHEMA, FailureClass
from core.shared_dispatch import SharedDispatcher, SharedDispatchRequest


@dataclass(frozen=True)
class SchedulerEngineConfig:
    max_lookback_seconds: int = 24 * 60 * 60
    max_occurrences_per_schedule: int = 256
    misfire_grace_seconds: int = 120
    stale_running_after_seconds: int = 60 * 60
    max_dispatches_per_tick: int = 16
    max_attempts: int = 2

    def __post_init__(self) -> None:
        for field_name in (
            "max_lookback_seconds",
            "max_occurrences_per_schedule",
            "misfire_grace_seconds",
            "stale_running_after_seconds",
            "max_dispatches_per_tick",
            "max_attempts",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")


class SchedulerEngine:
    def __init__(
        self,
        store: SchedulerStore,
        config: SchedulerEngineConfig | None = None,
    ) -> None:
        self.store = store
        self.config = config or SchedulerEngineConfig()

    def tick(
        self, *, now: int | None = None, dispatcher: SharedDispatcher | None = None
    ) -> dict[str, Any]:
        current = int(time.time()) if now is None else now
        if isinstance(current, bool) or not isinstance(current, int) or current < 0:
            raise ValueError("now must be a non-negative integer")
        self.store.initialize()
        recovered = self.store.recover_stale_running(
            now=current,
            stale_after_seconds=self.config.stale_running_after_seconds,
            max_attempts=self.config.max_attempts,
        )
        resumed_dependencies = self.store.resume_waiting_dependencies(now=current)
        counters: Counter[str] = Counter()
        replayed = 0
        evaluated = 0

        for schedule in self.store.list_enabled():
            evaluated += 1
            cursor = schedule.last_evaluated_at
            if cursor is None:
                cursor = max(0, current - 60)
            lower_bound = max(cursor, max(0, current - self.config.max_lookback_seconds))
            due = iter_due_times(
                schedule.spec,
                after_exclusive=lower_bound,
                until_inclusive=current,
                limit=self.config.max_occurrences_per_schedule,
            )
            for scheduled_for, forced_state, forced_reason in self._apply_misfire_policy(
                schedule, due, current
            ):
                occurrence_id = stable_occurrence_id(
                    schedule.spec.schedule_id, schedule.version, scheduled_for
                )
                proposal = build_execution_proposal(
                    schedule,
                    occurrence_id=occurrence_id,
                    scheduled_for=scheduled_for,
                )
                state, reason = (
                    (forced_state, forced_reason)
                    if forced_state is not None
                    else self._initial_state(schedule)
                )
                record, created = self.store.create_occurrence(
                    occurrence_id=occurrence_id,
                    schedule=schedule,
                    scheduled_for=scheduled_for,
                    state=state,
                    reason=reason,
                    proposal=proposal,
                    now=current,
                )
                if created:
                    counters[record.state] += 1
                else:
                    replayed += 1
            self.store.advance_cursor(
                schedule.spec.schedule_id,
                expected_version=schedule.version,
                evaluated_at=current,
            )

        dispatch_receipt = (
            self.dispatch_pending(dispatcher=dispatcher, now=current)
            if dispatcher is not None
            else {
                "claimed": 0,
                "done": 0,
                "retried": 0,
                "waiting_dependency": 0,
                "needs_operator": 0,
                "failed": 0,
                "continued": 0,
            }
        )

        return {
            "schema": TICK_RECEIPT_SCHEMA,
            "status": "DONE",
            "evaluated_schedules": evaluated,
            "created_occurrences": sum(counters.values()),
            "replayed_occurrences": replayed,
            "recovered_stale_running": recovered["retried"] + recovered["needs_operator"],
            "retried_stale_running": recovered["retried"],
            "stale_running_needs_operator": recovered["needs_operator"],
            "resumed_waiting_dependencies": resumed_dependencies,
            "dispatch": dispatch_receipt,
            "states": {
                state: counters.get(state, 0)
                for state in (
                    "pending", "running", "done", "failed",
                    "needs_operator", "skipped",
                )
            },
            "public_safe": True,
            "private_payloads_included": False,
            "external_side_effects_executed": False,
        }

    def dispatch_pending(
        self, *, dispatcher: SharedDispatcher, now: int | None = None
    ) -> dict[str, int]:
        current = int(time.time()) if now is None else now
        if isinstance(current, bool) or not isinstance(current, int) or current < 0:
            raise ValueError("now must be a non-negative integer")
        counters: Counter[str] = Counter()
        claimed_this_tick: set[str] = set()
        for _ in range(self.config.max_dispatches_per_tick):
            occurrence = self.store.claim_next_pending(
                now=current,
                exclude_occurrence_ids=frozenset(claimed_this_tick),
            )
            if occurrence is None:
                break
            claimed_this_tick.add(occurrence.occurrence_id)
            counters["claimed"] += 1
            proposal = occurrence.proposal
            dependency = proposal.get("payload", {}).get("wait_for")
            if isinstance(dependency, str):
                dependency_record = self.store.get_occurrence(dependency)
                if dependency_record is None or dependency_record.state != "done":
                    self.store.record_dispatch_receipt(
                        occurrence_id=occurrence.occurrence_id,
                        attempt=occurrence.attempt,
                        idempotency_key=occurrence.idempotency_key or "",
                        status="waiting_dependency",
                        reason="WAITING_DEPENDENCY",
                        evidence_ref=f"dependency:{dependency}",
                        result={
                            "schema": "skeleton.scheduler_dispatch_receipt.v1",
                            "occurrence_id": occurrence.occurrence_id,
                            "attempt": occurrence.attempt,
                            "idempotency_key": occurrence.idempotency_key,
                            "reason": "WAITING_DEPENDENCY",
                            "public_safe": True,
                            "external_side_effects_executed": False,
                        },
                        now=current,
                        parent_receipt_id=occurrence.parent_receipt_id,
                    )
                    self.store.transition_occurrence(
                        occurrence.occurrence_id,
                        expected_states={"running"},
                        new_state="waiting_dependency",
                        reason="WAITING_DEPENDENCY",
                        now=current,
                    )
                    counters["waiting_dependency"] += 1
                    continue
            request = SharedDispatchRequest(
                occurrence_id=occurrence.occurrence_id,
                route_type=str(proposal.get("route_type", "")),
                route_id=str(proposal.get("route_id", "")),
                payload=proposal.get("payload", {}),
                attempt=occurrence.attempt,
                idempotency_key=occurrence.idempotency_key or "",
                parent_receipt_id=occurrence.parent_receipt_id,
            )
            result = dispatcher.dispatch(request)
            receipt_id = self.store.record_dispatch_receipt(
                occurrence_id=occurrence.occurrence_id,
                attempt=occurrence.attempt,
                idempotency_key=occurrence.idempotency_key or "",
                status=result.status,
                reason=result.reason,
                evidence_ref=result.evidence_ref,
                result=result.receipt,
                now=current,
                parent_receipt_id=occurrence.parent_receipt_id,
            )
            final_status = self._complete_dispatch(
                occurrence=occurrence,
                dispatch=result,
                receipt_id=receipt_id,
                now=current,
            )
            counters[final_status] += 1
            if result.next_step is not None:
                counters["continued"] += self._create_followup(
                    occurrence=occurrence,
                    next_payload=result.next_step,
                    parent_receipt_id=receipt_id,
                    now=current,
                )
        return {
            "claimed": counters["claimed"],
            "done": counters["done"],
            "retried": counters["retried"],
            "waiting_dependency": counters["waiting_dependency"],
            "needs_operator": counters["needs_operator"],
            "failed": counters["failed"],
            "continued": counters["continued"],
        }

    def _complete_dispatch(self, *, occurrence, dispatch, receipt_id: str, now: int) -> str:
        if dispatch.status == "done":
            self.store.transition_occurrence(
                occurrence.occurrence_id,
                expected_states={"running"},
                new_state="done",
                reason="DISPATCH_DONE",
                now=now,
            )
            return "done"
        if dispatch.status == "waiting_dependency":
            proposal = dict(occurrence.proposal)
            proposal["wait_for"] = dispatch.waiting_dependency
            self.store.transition_occurrence(
                occurrence.occurrence_id,
                expected_states={"running"},
                new_state="waiting_dependency",
                reason="WAITING_DEPENDENCY",
                now=now,
            )
            return "waiting_dependency"
        if dispatch.status == "needs_operator":
            self.store.transition_occurrence(
                occurrence.occurrence_id,
                expected_states={"running"},
                new_state="needs_operator",
                reason="DISPATCH_NEEDS_OPERATOR",
                now=now,
            )
            return "needs_operator"
        if dispatch.retryable and occurrence.attempt < self.config.max_attempts:
            self.store.transition_occurrence(
                occurrence.occurrence_id,
                expected_states={"running"},
                new_state="pending",
                reason="DISPATCH_RETRY",
                now=now,
            )
            return "retried"
        self.store.transition_occurrence(
            occurrence.occurrence_id,
            expected_states={"running"},
            new_state="needs_operator",
            reason="DISPATCH_AUTOMATIC_PATHS_EXHAUSTED",
            now=now,
        )
        return "needs_operator"

    def _create_followup(
        self,
        *,
        occurrence,
        next_payload: dict[str, Any],
        parent_receipt_id: str,
        now: int,
    ) -> int:
        schedule = self.store.get_current(occurrence.schedule_id)
        if schedule is None or schedule.version != occurrence.schedule_version:
            return 0
        workflow = next_payload.get("deterministic_workflow", {})
        index = workflow.get("index") if isinstance(workflow, dict) else None
        step_id = f"step:{index}" if isinstance(index, int) else "step:next"
        occurrence_id = stable_followup_occurrence_id(occurrence.occurrence_id, step_id)
        scheduled_for = max(now, occurrence.scheduled_for) + (
            index if isinstance(index, int) and index > 0 else 1
        )
        proposal = {
            "schema": "skeleton.scheduler_execution_proposal.v1",
            "occurrence_id": occurrence_id,
            "schedule_id": occurrence.schedule_id,
            "schedule_version": occurrence.schedule_version,
            "scheduled_for": scheduled_for,
            "route_type": schedule.spec.route_type,
            "route_id": schedule.spec.route_id,
            "approval_policy": schedule.spec.approval_policy,
            "payload": next_payload,
            "authority": {
                "proposal_only": True,
                "external_side_effects_executed": False,
                "runner_enqueued": False,
                "loop_started": False,
            },
            "parent_occurrence_id": occurrence.occurrence_id,
            "parent_receipt_id": parent_receipt_id,
        }
        _, created = self.store.create_occurrence(
            occurrence_id=occurrence_id,
            schedule=schedule,
            scheduled_for=scheduled_for,
            state="pending",
            reason="CONTINUATION_DISPATCH_REQUIRED",
            proposal=proposal,
            now=now,
            parent_occurrence_id=occurrence.occurrence_id,
            parent_receipt_id=parent_receipt_id,
        )
        return int(created)

    def _apply_misfire_policy(
        self,
        schedule: StoredSchedule,
        due: tuple[int, ...],
        now: int,
    ) -> tuple[tuple[int, str | None, str | None], ...]:
        if not due:
            return ()
        delayed = tuple(
            scheduled_for
            for scheduled_for in due
            if now - scheduled_for > self.config.misfire_grace_seconds
        )
        delayed_set = set(delayed)
        on_time = tuple(item for item in due if item not in delayed_set)
        output: list[tuple[int, str | None, str | None]] = []
        if delayed:
            policy = schedule.spec.misfire_policy
            if policy == "skip":
                output.extend((item, "skipped", "MISFIRE_SKIP") for item in delayed)
            elif policy == "needs_operator":
                output.extend(
                    (item, "needs_operator", "MISFIRE_NEEDS_OPERATOR")
                    for item in delayed
                )
            else:
                output.extend(
                    (item, "skipped", "MISFIRE_COALESCED") for item in delayed[:-1]
                )
                output.append((delayed[-1], None, None))
        output.extend((item, None, None) for item in on_time)
        return tuple(output)

    def _initial_state(self, schedule: StoredSchedule) -> tuple[str, str]:
        overlap = self.store.active_counts(schedule.spec.schedule_id)
        pending = overlap["pending"]
        running = overlap["running"]
        if pending or running:
            if schedule.spec.overlap_policy == "skip":
                return "skipped", "OVERLAP_SKIP"
            if schedule.spec.overlap_policy == "needs_operator":
                return "needs_operator", "OVERLAP_NEEDS_OPERATOR"
            if pending > 0 or running == 0:
                return "skipped", "OVERLAP_QUEUE_FULL"

        if schedule.spec.approval_policy == "notify_only":
            return "done", "NOTIFY_ONLY_PROPOSAL"
        if schedule.spec.approval_policy == "require_operator_each_occurrence":
            return "needs_operator", "OPERATOR_REQUIRED"
        return "pending", "DISPATCH_REQUIRED"


@dataclass(frozen=True)
class CodegenRecoverySchedulingResult:
    recovery_occurrence_id: str
    consumer_occurrence_id: str
    failure_key: str
    recovery_created: bool
    consumer_created: bool


def schedule_codegen_runtime_recovery(
    store: SchedulerStore,
    *,
    issue_number: int,
    failure_signature: str,
    now: int,
) -> CodegenRecoverySchedulingResult:
    if isinstance(issue_number, bool) or not isinstance(issue_number, int) or issue_number <= 0:
        raise ValueError("issue_number must be a positive integer")
    if not isinstance(failure_signature, str) or not failure_signature:
        raise ValueError("failure_signature must be non-empty")
    if isinstance(now, bool) or not isinstance(now, int) or now < 0:
        raise ValueError("now must be a non-negative integer")
    safe_signature = "".join(
        character if character.isalnum() or character in "._:-" else "-"
        for character in failure_signature.lower()
    )[:64].strip(".:-")
    if not safe_signature:
        raise ValueError("failure_signature must contain a safe token")

    store.initialize()
    failure_key = f"control:codegen-runtime:{safe_signature}"
    recovery_schedule, _ = store.register(
        _codegen_recovery_schedule(safe_signature, failure_key),
        now=now,
    )
    recovery_occurrence_id = stable_occurrence_id(
        recovery_schedule.spec.schedule_id,
        recovery_schedule.version,
        0,
    )
    recovery_proposal = build_execution_proposal(
        recovery_schedule,
        occurrence_id=recovery_occurrence_id,
        scheduled_for=0,
    )
    _, recovery_created = store.create_occurrence(
        occurrence_id=recovery_occurrence_id,
        schedule=recovery_schedule,
        scheduled_for=0,
        state="pending",
        reason="CODEGEN_RUNTIME_RECOVERY_REQUIRED",
        proposal=recovery_proposal,
        now=now,
    )

    consumer_schedule, _ = store.register(
        _codegen_consumer_schedule(issue_number, recovery_occurrence_id),
        now=now,
    )
    consumer_occurrence_id = stable_occurrence_id(
        consumer_schedule.spec.schedule_id,
        consumer_schedule.version,
        0,
    )
    consumer_proposal = build_execution_proposal(
        consumer_schedule,
        occurrence_id=consumer_occurrence_id,
        scheduled_for=0,
    )
    _, consumer_created = store.create_occurrence(
        occurrence_id=consumer_occurrence_id,
        schedule=consumer_schedule,
        scheduled_for=0,
        state="waiting_dependency",
        reason="WAITING_RECOVERY",
        proposal=consumer_proposal,
        now=now,
        parent_occurrence_id=recovery_occurrence_id,
    )
    return CodegenRecoverySchedulingResult(
        recovery_occurrence_id=recovery_occurrence_id,
        consumer_occurrence_id=consumer_occurrence_id,
        failure_key=failure_key,
        recovery_created=recovery_created,
        consumer_created=consumer_created,
    )


def _codegen_recovery_schedule(signature: str, failure_key: str) -> ScheduleSpec:
    return ScheduleSpec.from_mapping(
        {
            "schema": "skeleton.schedule.v1",
            "schedule_id": f"control.codegen-runtime.{signature}",
            "trigger_kind": "once",
            "cron_expression": None,
            "once_at": 0,
            "timezone": "UTC",
            "route_type": "workflow",
            "route_id": "control_recovery",
            "approval_policy": "auto_run_low_risk",
            "overlap_policy": "queue_one",
            "misfire_policy": "run_once",
            "payload": {
                "privacy_boundary": "PUBLIC_SAFE_CODE_AND_SYNTHETIC_TESTS_ONLY",
                "bounded": True,
                "approved_capabilities": ["control:recovery"],
                "requested_capabilities": ["control:recovery"],
                "recovery_packet": {
                    "schema": CONTROL_RECOVERY_SCHEMA,
                    "failure_class": FailureClass.CODEGEN_RUNTIME_UNHEALTHY.value,
                    "failure_key": failure_key,
                    "backoff_seconds": 60,
                    "max_attempts": 3,
                },
            },
        }
    )


def _codegen_consumer_schedule(issue_number: int, recovery_occurrence_id: str) -> ScheduleSpec:
    return ScheduleSpec.from_mapping(
        {
            "schema": "skeleton.schedule.v1",
            "schedule_id": f"runner.codegen.issue-{issue_number}",
            "trigger_kind": "once",
            "cron_expression": None,
            "once_at": 0,
            "timezone": "UTC",
            "route_type": "runner",
            "route_id": "codegen_task",
            "approval_policy": "auto_run_low_risk",
            "overlap_policy": "queue_one",
            "misfire_policy": "run_once",
            "payload": {
                "privacy_boundary": "PUBLIC_SAFE_CONTROL_STATUS_ONLY",
                "bounded": True,
                "issue_number": issue_number,
                "consumer": "github_issue_runner_codegen",
                "wait_for": recovery_occurrence_id,
                "private_payload_included": False,
            },
        }
    )
