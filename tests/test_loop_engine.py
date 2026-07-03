from __future__ import annotations

from pathlib import Path

import pytest

from core.loop_controller import LoopDecision, LoopEvent, LoopPolicy, LoopState
from core.loop_engine import LoopEngine
from core.loop_state_store import LoopStateConflictError, LoopStateStore


def _engine(
    tmp_path: Path,
    *,
    policy: LoopPolicy | None = None,
) -> tuple[LoopEngine, LoopStateStore]:
    store = LoopStateStore(tmp_path / "loop-state.sqlite")
    store.initialize()
    return LoopEngine(store, policy or LoopPolicy()), store


def test_create_and_advance_persisted_loop(tmp_path: Path) -> None:
    engine, store = _engine(tmp_path)
    created = engine.create(run_id="run-1", task_id="task-1", recorded_at=1)
    prepared = engine.step(
        run_id="run-1",
        event=LoopEvent.PREPARED,
        recorded_at=2,
        expected_version=0,
    )
    started = engine.step(
        run_id="run-1",
        event=LoopEvent.STARTED,
        recorded_at=3,
        expected_version=1,
    )

    assert created.context.state is LoopState.CREATED
    assert prepared.run.context.state is LoopState.READY
    assert started.run.context.state is LoopState.RUNNING
    assert started.run.version == 2
    assert [event.reason for event in store.list_events("run-1")] == [
        "LOOP_PREPARED",
        "LOOP_STARTED",
    ]


def test_rejected_transition_is_returned_and_audited(tmp_path: Path) -> None:
    engine, store = _engine(tmp_path)
    engine.create(run_id="run-reject", task_id="task-reject", recorded_at=1)

    step = engine.step(
        run_id="run-reject",
        event=LoopEvent.COMPLETE,
        recorded_at=2,
    )

    assert step.accepted is False
    assert step.decision is LoopDecision.REJECT
    assert step.reason == "ILLEGAL_TRANSITION"
    assert step.run.context.state is LoopState.CREATED
    assert store.list_events("run-reject")[0].accepted is False


def test_checkpoint_resume_across_engine_instances(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path)
    engine.create(run_id="run-resume", task_id="task-resume", recorded_at=1)
    engine.step(run_id="run-resume", event=LoopEvent.PREPARED, recorded_at=2)
    engine.step(run_id="run-resume", event=LoopEvent.STARTED, recorded_at=3)
    checkpoint = engine.step(
        run_id="run-resume",
        event=LoopEvent.CHECKPOINT,
        recorded_at=4,
    )
    assert checkpoint.run.context.state is LoopState.CHECKPOINTED

    second_store = LoopStateStore(tmp_path / "loop-state.sqlite")
    second_engine = LoopEngine(second_store, LoopPolicy())
    resumed = second_engine.step(
        run_id="run-resume",
        event=LoopEvent.STARTED,
        recorded_at=5,
        expected_version=3,
    )

    assert resumed.run.context.state is LoopState.RUNNING
    assert resumed.reason == "LOOP_RESUMED"
    assert resumed.run.version == 4


def test_stale_expected_version_fails_before_append(tmp_path: Path) -> None:
    engine, store = _engine(tmp_path)
    engine.create(run_id="run-stale", task_id="task-stale", recorded_at=1)
    engine.step(run_id="run-stale", event=LoopEvent.PREPARED, recorded_at=2)

    with pytest.raises(LoopStateConflictError, match="expected version conflict"):
        engine.step(
            run_id="run-stale",
            event=LoopEvent.STARTED,
            recorded_at=3,
            expected_version=0,
        )

    assert store.load_run("run-stale").version == 1
    assert len(store.list_events("run-stale")) == 1


def test_budget_decision_is_persisted_but_not_executed(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path, policy=LoopPolicy(max_budget_units=1))
    engine.create(run_id="run-budget", task_id="task-budget", recorded_at=1)
    step = engine.step(
        run_id="run-budget",
        event=LoopEvent.PREPARED,
        recorded_at=2,
        budget_delta=2,
    )

    assert step.run.context.state is LoopState.NEEDS_OPERATOR
    assert step.decision is LoopDecision.ESCALATE
    assert step.reason == "BUDGET_EXHAUSTED"


def test_duplicate_create_and_bad_inputs_fail_closed(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path)
    engine.create(run_id="run-duplicate", task_id="task-duplicate", recorded_at=1)

    with pytest.raises(LoopStateConflictError):
        engine.create(run_id="run-duplicate", task_id="task-duplicate", recorded_at=2)
    with pytest.raises(TypeError):
        LoopEngine(object(), LoopPolicy())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        engine.step(
            run_id="run-duplicate",
            event=LoopEvent.PREPARED,
            recorded_at=2,
            expected_version=-1,
        )
