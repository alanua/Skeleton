from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from core.loop_controller import (
    LoopContext,
    LoopDecision,
    LoopEvent,
    LoopPolicy,
    LoopResult,
    LoopState,
    advance_loop,
)
from core.loop_state_store import (
    LoopStateConflictError,
    LoopStateStore,
    StoredLoopRun,
)


@dataclass(frozen=True)
class LoopStepResult:
    run: StoredLoopRun
    transition: LoopResult

    @property
    def accepted(self) -> bool:
        return self.transition.accepted

    @property
    def decision(self) -> LoopDecision:
        return self.transition.decision

    @property
    def reason(self) -> str:
        return self.transition.reason


class LoopEngine:
    """Compose deterministic transition logic with operational persistence."""

    def __init__(
        self,
        store: LoopStateStore,
        policy: LoopPolicy,
        dependency_resolver: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    ) -> None:
        if not isinstance(store, LoopStateStore):
            raise TypeError("store must be LoopStateStore")
        if not isinstance(policy, LoopPolicy):
            raise TypeError("policy must be LoopPolicy")
        self.store = store
        self.policy = policy
        self.dependency_resolver = dependency_resolver

    def create(
        self,
        *,
        run_id: str,
        task_id: str,
        recorded_at: int,
        context: LoopContext | None = None,
        dependency: Mapping[str, Any] | None = None,
    ) -> StoredLoopRun:
        initial = context if context is not None else LoopContext()
        if not isinstance(initial, LoopContext):
            raise TypeError("context must be LoopContext")
        if dependency is not None and self.dependency_resolver is not None:
            state = self.dependency_resolver(dependency)
            if state.get("verified") is not True:
                initial = LoopContext(state=LoopState.BLOCKED)
        return self.store.create_run(
            run_id=run_id,
            task_id=task_id,
            context=initial,
            recorded_at=recorded_at,
        )

    def step(
        self,
        *,
        run_id: str,
        event: LoopEvent,
        recorded_at: int,
        now: int | None = None,
        budget_delta: int = 0,
        expected_version: int | None = None,
    ) -> LoopStepResult:
        current = self.store.load_run(run_id)
        if expected_version is not None:
            _non_negative_int(expected_version, "expected_version")
            if current.version != expected_version:
                raise LoopStateConflictError("loop engine expected version conflict")

        transition = advance_loop(
            current.context,
            event,
            self.policy,
            now=now,
            budget_delta=budget_delta,
        )
        stored = self.store.append_result(
            run_id=run_id,
            expected_version=current.version,
            result=transition,
            recorded_at=recorded_at,
        )
        return LoopStepResult(run=stored, transition=transition)


def _non_negative_int(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
