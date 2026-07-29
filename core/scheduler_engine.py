from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import time
from typing import Any

from core.scheduler_models import (
    TICK_RECEIPT_SCHEMA,
    StoredSchedule,
    build_execution_proposal,
    iter_due_times,
    stable_occurrence_id,
)
from core.scheduler_store import SchedulerStore


@dataclass(frozen=True)
class SchedulerEngineConfig:
    max_lookback_seconds: int = 24 * 60 * 60
    max_occurrences_per_schedule: int = 256
    misfire_grace_seconds: int = 120
    stale_running_after_seconds: int = 60 * 60

    def __post_init__(self) -> None:
        for field_name in (
            "max_lookback_seconds",
            "max_occurrences_per_schedule",
            "misfire_grace_seconds",
            "stale_running_after_seconds",
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

    def tick(self, *, now: int | None = None) -> dict[str, Any]:
        current = int(time.time()) if now is None else now
        if isinstance(current, bool) or not isinstance(current, int) or current < 0:
            raise ValueError("now must be a non-negative integer")
        self.store.initialize()
        recovered = self.store.recover_stale_running(
            now=current,
            stale_after_seconds=self.config.stale_running_after_seconds,
        )
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

        return {
            "schema": TICK_RECEIPT_SCHEMA,
            "status": "DONE",
            "evaluated_schedules": evaluated,
            "created_occurrences": sum(counters.values()),
            "replayed_occurrences": replayed,
            "recovered_stale_running": recovered,
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
