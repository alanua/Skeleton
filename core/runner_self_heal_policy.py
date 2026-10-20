from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal


SelfHealAction = Literal[
    "retry",
    "wait",
    "clean-disposable-worktree",
    "supersede",
    "validate",
    "needs-operator",
]

ValidationState = Literal[
    "not-required",
    "not-run",
    "passed",
    "temporary-failure",
    "permanent-failure",
]

RETRY: Final[SelfHealAction] = "retry"
WAIT: Final[SelfHealAction] = "wait"
CLEAN_DISPOSABLE_WORKTREE: Final[SelfHealAction] = (
    "clean-disposable-worktree"
)
SUPERSEDE: Final[SelfHealAction] = "supersede"
VALIDATE: Final[SelfHealAction] = "validate"
NEEDS_OPERATOR_ACTION: Final[SelfHealAction] = "needs-operator"

_VALIDATION_STATES: Final = frozenset(
    {
        "not-required",
        "not-run",
        "passed",
        "temporary-failure",
        "permanent-failure",
    }
)


@dataclass(frozen=True, slots=True)
class QueueTaskState:
    """Synthetic queue state for deterministic self-heal diagnostics.

    The policy is intentionally descriptive: callers may execute a returned
    action only after their normal protected/operator gates have allowed it.
    """

    task_id: str
    lifecycle: str = "blocked"
    disposable_worktree: bool = False
    worktree_dirty: bool = False
    base_stale: bool = False
    validation_state: ValidationState = "not-required"
    current_blocker_signature: str | None = None
    prior_blocker_signatures: tuple[str, ...] = ()
    protected_merge_boundary: bool = False
    operator_gate_required: bool = False
    superseded_by: str | None = None
    dependency_waiting: bool = False
    lease_active: bool = False
    temporary_validation_failures: int = 0
    max_temporary_validation_retries: int = 2

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id_required")
        if self.validation_state not in _VALIDATION_STATES:
            raise ValueError("invalid_validation_state")
        if self.temporary_validation_failures < 0:
            raise ValueError("invalid_temporary_validation_failures")
        if self.max_temporary_validation_retries < 0:
            raise ValueError("invalid_max_temporary_validation_retries")


@dataclass(frozen=True, slots=True)
class SelfHealDecision:
    action: SelfHealAction
    reason: str
    blocked_by_gate: bool = False


def decide_queue_self_heal_action(state: QueueTaskState) -> SelfHealDecision:
    """Classify the next safe action without performing that action."""

    lifecycle = state.lifecycle.strip().lower()

    if state.protected_merge_boundary:
        return SelfHealDecision(
            NEEDS_OPERATOR_ACTION,
            "protected_merge_boundary",
            blocked_by_gate=True,
        )

    if state.operator_gate_required:
        return SelfHealDecision(
            NEEDS_OPERATOR_ACTION,
            "operator_gate_required",
            blocked_by_gate=True,
        )

    if state.superseded_by:
        return SelfHealDecision(SUPERSEDE, "newer_task_supersedes_current")

    if state.lease_active or lifecycle == "running":
        return SelfHealDecision(WAIT, "active_runner_lease")

    if state.dependency_waiting:
        return SelfHealDecision(WAIT, "waiting_dependency")

    if _has_repeated_identical_blocker(state):
        return SelfHealDecision(NEEDS_OPERATOR_ACTION, "repeated_identical_blocker")

    if state.worktree_dirty:
        if state.disposable_worktree:
            return SelfHealDecision(
                CLEAN_DISPOSABLE_WORKTREE,
                "dirty_disposable_worktree",
            )
        return SelfHealDecision(
            NEEDS_OPERATOR_ACTION,
            "dirty_non_disposable_worktree",
            blocked_by_gate=True,
        )

    if state.base_stale:
        return SelfHealDecision(WAIT, "stale_base_requires_refresh")

    if state.validation_state == "not-run":
        return SelfHealDecision(VALIDATE, "validation_not_run")

    if state.validation_state == "temporary-failure":
        if (
            state.temporary_validation_failures
            <= state.max_temporary_validation_retries
        ):
            return SelfHealDecision(RETRY, "temporary_validation_failure")
        return SelfHealDecision(
            NEEDS_OPERATOR_ACTION,
            "temporary_validation_retry_exhausted",
        )

    if state.validation_state == "permanent-failure":
        return SelfHealDecision(NEEDS_OPERATOR_ACTION, "permanent_validation_failure")

    if lifecycle in {"queued", "ready", "pending", "blocked", "failed"}:
        return SelfHealDecision(RETRY, "retryable_queue_state")

    return SelfHealDecision(WAIT, "no_safe_self_heal_action")


def _has_repeated_identical_blocker(state: QueueTaskState) -> bool:
    signature = (state.current_blocker_signature or "").strip()
    if not signature or len(state.prior_blocker_signatures) < 2:
        return False
    latest_two = tuple(item.strip() for item in state.prior_blocker_signatures[-2:])
    return latest_two == (signature, signature)
