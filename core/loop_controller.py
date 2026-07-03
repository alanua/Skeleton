from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class LoopState(str, Enum):
    CREATED = "CREATED"
    READY = "READY"
    RUNNING = "RUNNING"
    CHECKPOINTED = "CHECKPOINTED"
    NEEDS_OPERATOR = "NEEDS_OPERATOR"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    DONE = "DONE"


class LoopEvent(str, Enum):
    PREPARED = "PREPARED"
    STARTED = "STARTED"
    STEP_SUCCEEDED = "STEP_SUCCEEDED"
    STEP_FAILED = "STEP_FAILED"
    CHECKPOINT = "CHECKPOINT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    OPERATOR_REQUIRED = "OPERATOR_REQUIRED"
    CANCEL = "CANCEL"
    COMPLETE = "COMPLETE"
    TIMEOUT = "TIMEOUT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    LEASE_EXPIRED = "LEASE_EXPIRED"


class LoopDecision(str, Enum):
    CONTINUE = "CONTINUE"
    RETRY = "RETRY"
    CHECKPOINT = "CHECKPOINT"
    REVIEW = "REVIEW"
    ESCALATE = "ESCALATE"
    STOP = "STOP"
    REJECT = "REJECT"


HALTED = frozenset(
    {
        LoopState.NEEDS_OPERATOR,
        LoopState.HUMAN_REVIEW,
        LoopState.BLOCKED,
        LoopState.CANCELLED,
        LoopState.DONE,
    }
)
ACTIVE = frozenset(
    {LoopState.CREATED, LoopState.READY, LoopState.RUNNING, LoopState.CHECKPOINTED}
)
LEASED = frozenset({LoopState.READY, LoopState.RUNNING, LoopState.CHECKPOINTED})


@dataclass(frozen=True)
class LoopPolicy:
    max_iterations: int = 10
    retry_limit: int = 2
    max_budget_units: int = 100
    failure_exhaustion_state: LoopState = LoopState.BLOCKED

    def __post_init__(self) -> None:
        _positive(self.max_iterations, "max_iterations")
        _non_negative(self.retry_limit, "retry_limit")
        _non_negative(self.max_budget_units, "max_budget_units")
        if self.failure_exhaustion_state not in {
            LoopState.BLOCKED,
            LoopState.NEEDS_OPERATOR,
        }:
            raise ValueError("invalid failure_exhaustion_state")


@dataclass(frozen=True)
class LoopContext:
    state: LoopState = LoopState.CREATED
    iterations: int = 0
    retries: int = 0
    budget_used: int = 0
    deadline_at: int | None = None
    lease_expires_at: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, LoopState):
            raise TypeError("state must be LoopState")
        for name in ("iterations", "retries", "budget_used"):
            _non_negative(getattr(self, name), name)
        for name in ("deadline_at", "lease_expires_at"):
            value = getattr(self, name)
            if value is not None:
                _non_negative(value, name)


@dataclass(frozen=True)
class LoopResult:
    previous: LoopContext
    current: LoopContext
    event: LoopEvent
    accepted: bool
    decision: LoopDecision
    reason: str

    @property
    def halted(self) -> bool:
        return self.current.state in HALTED


def advance_loop(
    current: LoopContext,
    event: LoopEvent,
    policy: LoopPolicy,
    *,
    now: int | None = None,
    budget_delta: int = 0,
) -> LoopResult:
    """Advance one deterministic loop step without external side effects."""
    if not isinstance(current, LoopContext):
        raise TypeError("current must be LoopContext")
    if not isinstance(event, LoopEvent):
        raise TypeError("event must be LoopEvent")
    if not isinstance(policy, LoopPolicy):
        raise TypeError("policy must be LoopPolicy")
    if now is not None:
        _non_negative(now, "now")
    _non_negative(budget_delta, "budget_delta")

    if current.state in HALTED:
        return _reject(current, event, "HALTED_STATE")

    budget = current.budget_used + budget_delta

    if event is LoopEvent.CANCEL:
        if current.state not in ACTIVE:
            return _reject(current, event, "ILLEGAL_TRANSITION")
        return _go(current, event, LoopState.CANCELLED, LoopDecision.STOP, "CANCELLED", budget=budget)
    if event is LoopEvent.OPERATOR_REQUIRED:
        return _go(current, event, LoopState.NEEDS_OPERATOR, LoopDecision.ESCALATE, "OPERATOR_REQUIRED", budget=budget)
    if event is LoopEvent.REVIEW_REQUIRED:
        return _go(current, event, LoopState.HUMAN_REVIEW, LoopDecision.REVIEW, "REVIEW_REQUIRED", budget=budget)
    if event is LoopEvent.TIMEOUT:
        return _go(current, event, LoopState.BLOCKED, LoopDecision.STOP, "TIMEOUT", budget=budget)
    if event is LoopEvent.BUDGET_EXHAUSTED:
        return _go(current, event, LoopState.NEEDS_OPERATOR, LoopDecision.ESCALATE, "BUDGET_EXHAUSTED", budget=budget)
    if event is LoopEvent.LEASE_EXPIRED:
        if current.state not in LEASED:
            return _reject(current, event, "ILLEGAL_TRANSITION")
        return _go(current, event, LoopState.CHECKPOINTED, LoopDecision.CHECKPOINT, "LEASE_EXPIRED", budget=budget)

    if current.deadline_at is not None and now is not None and now >= current.deadline_at:
        return _go(current, event, LoopState.BLOCKED, LoopDecision.STOP, "TIMEOUT", budget=budget)
    if current.lease_expires_at is not None and now is not None and now >= current.lease_expires_at and current.state in LEASED:
        return _go(current, event, LoopState.CHECKPOINTED, LoopDecision.CHECKPOINT, "LEASE_EXPIRED", budget=budget)
    if budget > policy.max_budget_units:
        return _go(current, event, LoopState.NEEDS_OPERATOR, LoopDecision.ESCALATE, "BUDGET_EXHAUSTED", budget=budget)
    if event in {LoopEvent.STARTED, LoopEvent.STEP_SUCCEEDED, LoopEvent.STEP_FAILED} and current.iterations >= policy.max_iterations:
        return _go(current, event, LoopState.BLOCKED, LoopDecision.STOP, "MAX_ITERATIONS_EXHAUSTED", budget=budget)

    if (current.state, event) == (LoopState.CREATED, LoopEvent.PREPARED):
        return _go(current, event, LoopState.READY, LoopDecision.CONTINUE, "LOOP_PREPARED", budget=budget)
    if (current.state, event) == (LoopState.READY, LoopEvent.STARTED):
        return _go(current, event, LoopState.RUNNING, LoopDecision.CONTINUE, "LOOP_STARTED", budget=budget)
    if (current.state, event) == (LoopState.CHECKPOINTED, LoopEvent.STARTED):
        return _go(current, event, LoopState.RUNNING, LoopDecision.CONTINUE, "LOOP_RESUMED", budget=budget)
    if (current.state, event) == (LoopState.RUNNING, LoopEvent.STEP_SUCCEEDED):
        return _go(current, event, LoopState.RUNNING, LoopDecision.CONTINUE, "STEP_SUCCEEDED", iterations=current.iterations + 1, retries=0, budget=budget)
    if (current.state, event) == (LoopState.RUNNING, LoopEvent.STEP_FAILED):
        iterations = current.iterations + 1
        retries = current.retries + 1
        if retries > policy.retry_limit:
            state = policy.failure_exhaustion_state
            decision = LoopDecision.ESCALATE if state is LoopState.NEEDS_OPERATOR else LoopDecision.STOP
            return _go(current, event, state, decision, "RETRY_LIMIT_EXHAUSTED", iterations=iterations, retries=retries, budget=budget)
        return _go(current, event, LoopState.RUNNING, LoopDecision.RETRY, "STEP_FAILED_RETRY", iterations=iterations, retries=retries, budget=budget)
    if (current.state, event) == (LoopState.RUNNING, LoopEvent.CHECKPOINT):
        return _go(current, event, LoopState.CHECKPOINTED, LoopDecision.CHECKPOINT, "CHECKPOINTED", budget=budget)
    if (current.state, event) == (LoopState.RUNNING, LoopEvent.COMPLETE):
        return _go(current, event, LoopState.DONE, LoopDecision.STOP, "COMPLETED", budget=budget)

    return _reject(current, event, "ILLEGAL_TRANSITION")


def _go(
    previous: LoopContext,
    event: LoopEvent,
    state: LoopState,
    decision: LoopDecision,
    reason: str,
    *,
    iterations: int | None = None,
    retries: int | None = None,
    budget: int | None = None,
) -> LoopResult:
    current = replace(
        previous,
        state=state,
        iterations=previous.iterations if iterations is None else iterations,
        retries=previous.retries if retries is None else retries,
        budget_used=previous.budget_used if budget is None else budget,
    )
    return LoopResult(previous, current, event, True, decision, reason)


def _reject(current: LoopContext, event: LoopEvent, reason: str) -> LoopResult:
    return LoopResult(current, current, event, False, LoopDecision.REJECT, reason)


def _positive(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _non_negative(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
