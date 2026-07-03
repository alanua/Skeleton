from dataclasses import replace

import pytest

from core.loop_controller import LoopContext, LoopDecision, LoopEvent, LoopPolicy, LoopState, advance_loop


def running(**changes: object) -> LoopContext:
    return replace(LoopContext(state=LoopState.RUNNING), **changes)


def test_happy_path() -> None:
    policy = LoopPolicy()
    ready = advance_loop(LoopContext(), LoopEvent.PREPARED, policy)
    active = advance_loop(ready.current, LoopEvent.STARTED, policy)
    done = advance_loop(active.current, LoopEvent.COMPLETE, policy)
    assert [ready.current.state, active.current.state, done.current.state] == [LoopState.READY, LoopState.RUNNING, LoopState.DONE]


def test_checkpoint_and_resume() -> None:
    saved = advance_loop(running(iterations=2), LoopEvent.CHECKPOINT, LoopPolicy())
    resumed = advance_loop(saved.current, LoopEvent.STARTED, LoopPolicy())
    assert saved.current.state is LoopState.CHECKPOINTED
    assert resumed.current.state is LoopState.RUNNING
    assert resumed.reason == "LOOP_RESUMED"
    assert resumed.current.iterations == 2


@pytest.mark.parametrize("state", [LoopState.CREATED, LoopState.READY, LoopState.RUNNING, LoopState.CHECKPOINTED])
def test_cancel_active_states(state: LoopState) -> None:
    assert advance_loop(LoopContext(state=state), LoopEvent.CANCEL, LoopPolicy()).current.state is LoopState.CANCELLED


def test_illegal_and_halted_transitions_do_not_mutate() -> None:
    created = LoopContext()
    illegal = advance_loop(created, LoopEvent.COMPLETE, LoopPolicy())
    done = LoopContext(state=LoopState.DONE, iterations=3)
    halted = advance_loop(done, LoopEvent.STARTED, LoopPolicy())
    assert (illegal.accepted, illegal.reason, illegal.current is created) == (False, "ILLEGAL_TRANSITION", True)
    assert (halted.accepted, halted.reason, halted.current is done) == (False, "HALTED_STATE", True)


def test_retry_and_exhaustion() -> None:
    policy = LoopPolicy(retry_limit=1, failure_exhaustion_state=LoopState.NEEDS_OPERATOR)
    first = advance_loop(running(), LoopEvent.STEP_FAILED, policy)
    second = advance_loop(first.current, LoopEvent.STEP_FAILED, policy)
    assert first.decision is LoopDecision.RETRY
    assert first.current.retries == 1
    assert second.current.state is LoopState.NEEDS_OPERATOR
    assert second.reason == "RETRY_LIMIT_EXHAUSTED"
    assert second.current.retries == 2


def test_success_resets_retry_and_increments_iteration() -> None:
    result = advance_loop(running(iterations=2, retries=2), LoopEvent.STEP_SUCCEEDED, LoopPolicy())
    assert (result.current.iterations, result.current.retries) == (3, 0)


def test_iteration_timeout_budget_and_lease_limits() -> None:
    maximum = advance_loop(running(iterations=1), LoopEvent.STEP_SUCCEEDED, LoopPolicy(max_iterations=1))
    timeout = advance_loop(running(deadline_at=10), LoopEvent.STEP_SUCCEEDED, LoopPolicy(), now=10)
    budget = advance_loop(running(budget_used=4), LoopEvent.STEP_SUCCEEDED, LoopPolicy(max_budget_units=5), budget_delta=2)
    lease = advance_loop(running(lease_expires_at=20), LoopEvent.STEP_SUCCEEDED, LoopPolicy(), now=20)
    assert (maximum.current.state, maximum.reason) == (LoopState.BLOCKED, "MAX_ITERATIONS_EXHAUSTED")
    assert (timeout.current.state, timeout.reason) == (LoopState.BLOCKED, "TIMEOUT")
    assert (budget.current.state, budget.reason, budget.current.budget_used) == (LoopState.NEEDS_OPERATOR, "BUDGET_EXHAUSTED", 6)
    assert (lease.current.state, lease.reason) == (LoopState.CHECKPOINTED, "LEASE_EXPIRED")


def test_explicit_limit_events() -> None:
    timeout = advance_loop(running(), LoopEvent.TIMEOUT, LoopPolicy())
    budget = advance_loop(running(), LoopEvent.BUDGET_EXHAUSTED, LoopPolicy())
    lease = advance_loop(running(), LoopEvent.LEASE_EXPIRED, LoopPolicy())
    assert timeout.current.state is LoopState.BLOCKED
    assert budget.current.state is LoopState.NEEDS_OPERATOR
    assert lease.current.state is LoopState.CHECKPOINTED


def test_review_and_operator_events() -> None:
    review = advance_loop(running(), LoopEvent.REVIEW_REQUIRED, LoopPolicy())
    operator = advance_loop(running(), LoopEvent.OPERATOR_REQUIRED, LoopPolicy())
    assert (review.current.state, review.decision) == (LoopState.HUMAN_REVIEW, LoopDecision.REVIEW)
    assert (operator.current.state, operator.decision) == (LoopState.NEEDS_OPERATOR, LoopDecision.ESCALATE)


def test_determinism_and_no_input_mutation() -> None:
    current = running(iterations=2, retries=1, budget_used=3)
    policy = LoopPolicy(max_iterations=8, retry_limit=3, max_budget_units=10)
    first = advance_loop(current, LoopEvent.STEP_FAILED, policy, now=50, budget_delta=2)
    second = advance_loop(current, LoopEvent.STEP_FAILED, policy, now=50, budget_delta=2)
    assert first == second
    assert current == running(iterations=2, retries=1, budget_used=3)
    assert first.current is not current


def test_validation() -> None:
    with pytest.raises(ValueError):
        LoopPolicy(max_iterations=0)
    with pytest.raises(ValueError):
        LoopPolicy(retry_limit=-1)
    with pytest.raises(ValueError):
        LoopContext(iterations=-1)
    with pytest.raises(ValueError):
        advance_loop(running(), LoopEvent.STEP_SUCCEEDED, LoopPolicy(), budget_delta=-1)
