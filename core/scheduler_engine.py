from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import threading
import time
from typing import Any
import uuid

from core.domain_event_graph import DomainEventGraph, DomainEventGraphError
from core.scheduler_models import (
    TICK_RECEIPT_SCHEMA,
    StoredSchedule,
    build_execution_proposal,
    iter_due_times,
    stable_followup_occurrence_id,
    stable_occurrence_id,
)
from core.scheduler_store import SchedulerStore
from core.shared_dispatch import SharedDispatcher, SharedDispatchRequest

PRODUCTION_SCHEDULER_DB = Path(
    "/home/agent/.local/state/skeleton-runner/scheduler/scheduler.sqlite3"
)


def production_scheduler_db_path() -> Path:
    return PRODUCTION_SCHEDULER_DB


@dataclass(frozen=True)
class SchedulerEngineConfig:
    max_lookback_seconds: int = 24 * 60 * 60
    max_occurrences_per_schedule: int = 256
    misfire_grace_seconds: int = 120
    stale_running_after_seconds: int = 60 * 60
    max_dispatches_per_tick: int = 16
    max_attempts: int = 2
    retry_backoff_seconds: int = 1
    max_retry_backoff_seconds: int = 5 * 60
    initial_lease_seconds: int = 60 * 60
    heartbeat_interval_seconds: int = 60

    def __post_init__(self) -> None:
        for field_name in (
            "max_lookback_seconds",
            "max_occurrences_per_schedule",
            "misfire_grace_seconds",
            "stale_running_after_seconds",
            "max_dispatches_per_tick",
            "max_attempts",
            "retry_backoff_seconds",
            "max_retry_backoff_seconds",
            "initial_lease_seconds",
            "heartbeat_interval_seconds",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")
        if self.heartbeat_interval_seconds >= self.initial_lease_seconds:
            raise ValueError("heartbeat_interval_seconds must be less than initial_lease_seconds")
        if self.retry_backoff_seconds > self.max_retry_backoff_seconds:
            raise ValueError("retry_backoff_seconds must not exceed max_retry_backoff_seconds")


class SchedulerEngine:
    def __init__(
        self,
        store: SchedulerStore,
        config: SchedulerEngineConfig | None = None,
        clock: Any | None = None,
        domain_event_graph: DomainEventGraph | None = None,
    ) -> None:
        self.store = store
        self.config = config or SchedulerEngineConfig()
        self._owner = f"scheduler-engine:{uuid.uuid4().hex}"
        self._clock = clock or time.time
        self._domain_event_graph = domain_event_graph

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
            retry_backoff_seconds=self._retry_backoff_seconds(attempt=1),
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
            "recovered_stale_running": (
                recovered["retried"] + recovered["needs_operator"] + recovered.get("finalized", 0)
            ),
            "retried_stale_running": recovered["retried"],
            "stale_running_needs_operator": recovered["needs_operator"],
            "finalized_stale_running": recovered.get("finalized", 0),
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
                owner=self._owner,
                lease_seconds=self.config.initial_lease_seconds,
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
                    self._record_dependency_wait(
                        occurrence_id=occurrence.occurrence_id,
                        dependency_id=dependency,
                        now=current,
                    )
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
                    self.store.mark_running_waiting_dependency(
                        occurrence.occurrence_id,
                        dependency_id=dependency,
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
            heartbeat_stop, heartbeat_thread = self._start_dispatch_heartbeat(
                occurrence.occurrence_id
            )
            try:
                result = dispatcher.dispatch(request)
            finally:
                heartbeat_stop.set()
                heartbeat_thread.join(timeout=max(1, self.config.heartbeat_interval_seconds))
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

    def _record_dependency_wait(self, *, occurrence_id: str, dependency_id: str, now: int) -> None:
        if self._domain_event_graph is None:
            return
        try:
            self._domain_event_graph.record_scheduler_dependency(
                occurrence_ref=occurrence_id,
                dependency_ref=dependency_id,
                observed_at=now,
                idempotency_key=f"scheduler-dependency-{occurrence_id}-{dependency_id}",
            )
        except DomainEventGraphError:
            return

    def _start_dispatch_heartbeat(
        self, occurrence_id: str
    ) -> tuple[threading.Event, threading.Thread]:
        stop = threading.Event()

        def run() -> None:
            while not stop.is_set():
                renewed = self.store.renew_running_claim(
                    occurrence_id,
                    owner=self._owner,
                    lease_seconds=self.config.initial_lease_seconds,
                    now=int(self._clock()),
                )
                if not renewed:
                    return
                stop.wait(self.config.heartbeat_interval_seconds)

        thread = threading.Thread(
            target=run,
            name=f"scheduler-heartbeat-{occurrence_id[:12]}",
            daemon=True,
        )
        thread.start()
        return stop, thread

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
            dependency = dispatch.waiting_dependency
            if not isinstance(dependency, str) or not dependency:
                self.store.transition_occurrence(
                    occurrence.occurrence_id,
                    expected_states={"running"},
                    new_state="needs_operator",
                    reason="WAITING_DEPENDENCY_MISSING_ID",
                    now=now,
                )
                return "needs_operator"
            self.store.mark_running_waiting_dependency(
                occurrence.occurrence_id,
                dependency_id=dependency,
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
        if _dispatch_is_ambiguous_mutating(dispatch.receipt):
            self.store.transition_occurrence(
                occurrence.occurrence_id,
                expected_states={"running"},
                new_state="needs_operator",
                reason="AMBIGUOUS_MUTATING_RECEIPT",
                now=now,
            )
            return "needs_operator"
        if dispatch.retryable and occurrence.attempt < self.config.max_attempts:
            self.store.defer_running_retry(
                occurrence.occurrence_id,
                reason="DISPATCH_RETRY",
                retry_after_at=now + self._retry_backoff_seconds(attempt=occurrence.attempt),
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

    def _retry_backoff_seconds(self, *, attempt: int) -> int:
        exponent = max(0, attempt - 1)
        return min(
            self.config.retry_backoff_seconds * (2 ** exponent),
            self.config.max_retry_backoff_seconds,
        )

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


def _dispatch_is_ambiguous_mutating(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    status = str(value.get("status") or "").upper()
    reason = str(value.get("reason") or "").upper()
    decision = str(value.get("decision") or "").upper()
    if (
        value.get("external_side_effects_executed") is True
        and (
            status in {"UNKNOWN", "AMBIGUOUS"}
            or decision in {"UNKNOWN", "AMBIGUOUS"}
            or "AMBIGUOUS" in reason
        )
    ):
        return True
    route_receipt = value.get("route_receipt")
    return _dispatch_is_ambiguous_mutating(route_receipt)
