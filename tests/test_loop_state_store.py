from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.loop_controller import LoopContext, LoopEvent, LoopPolicy, LoopState, advance_loop
from core.loop_state_store import (
    LoopStateConflictError,
    LoopStateCorruptionError,
    LoopStateStore,
)


def _store(tmp_path: Path) -> LoopStateStore:
    store = LoopStateStore(tmp_path / "loop-state.sqlite")
    store.initialize()
    return store


def _running_context() -> LoopContext:
    policy = LoopPolicy()
    ready = advance_loop(LoopContext(), LoopEvent.PREPARED, policy)
    return advance_loop(ready.current, LoopEvent.STARTED, policy).current


def test_create_and_load_run_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    context = LoopContext(deadline_at=100, lease_expires_at=20)
    created = store.create_run(
        run_id="run-1",
        task_id="issue-1461",
        context=context,
        recorded_at=1,
    )

    loaded = store.load_run("run-1")
    assert created == loaded
    assert loaded.version == 0
    assert loaded.context == context
    assert store.list_events("run-1") == []


def test_append_accepted_transition_and_read_back(tmp_path: Path) -> None:
    store = _store(tmp_path)
    current = LoopContext()
    store.create_run(run_id="run-2", task_id="task-2", context=current, recorded_at=1)
    result = advance_loop(current, LoopEvent.PREPARED, LoopPolicy())

    updated = store.append_result(
        run_id="run-2",
        expected_version=0,
        result=result,
        recorded_at=2,
    )
    events = store.list_events("run-2")

    assert updated.version == 1
    assert updated.context.state is LoopState.READY
    assert len(events) == 1
    assert events[0].previous == current
    assert events[0].current == result.current
    assert events[0].accepted is True
    assert events[0].reason == "LOOP_PREPARED"


def test_rejected_transition_is_append_only_without_state_change(tmp_path: Path) -> None:
    store = _store(tmp_path)
    current = LoopContext()
    store.create_run(run_id="run-reject", task_id="task-reject", context=current, recorded_at=1)
    result = advance_loop(current, LoopEvent.COMPLETE, LoopPolicy())
    assert result.accepted is False

    updated = store.append_result(
        run_id="run-reject",
        expected_version=0,
        result=result,
        recorded_at=2,
    )
    event = store.list_events("run-reject")[0]

    assert updated.version == 1
    assert updated.context == current
    assert event.accepted is False
    assert event.previous == event.current == current
    assert event.reason == "ILLEGAL_TRANSITION"


def test_checkpoint_and_resume_survive_reload(tmp_path: Path) -> None:
    store = _store(tmp_path)
    running = _running_context()
    store.create_run(run_id="run-checkpoint", task_id="task-checkpoint", context=running, recorded_at=1)

    checkpoint = advance_loop(running, LoopEvent.CHECKPOINT, LoopPolicy())
    saved = store.append_result(
        run_id="run-checkpoint",
        expected_version=0,
        result=checkpoint,
        recorded_at=2,
    )
    assert saved.context.state is LoopState.CHECKPOINTED

    loaded = store.load_run("run-checkpoint")
    resume = advance_loop(loaded.context, LoopEvent.STARTED, LoopPolicy())
    resumed = store.append_result(
        run_id="run-checkpoint",
        expected_version=1,
        result=resume,
        recorded_at=3,
    )

    assert resumed.version == 2
    assert resumed.context.state is LoopState.RUNNING
    assert [event.reason for event in store.list_events("run-checkpoint")] == [
        "CHECKPOINTED",
        "LOOP_RESUMED",
    ]


def test_stale_writer_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    current = LoopContext()
    store.create_run(run_id="run-stale", task_id="task-stale", context=current, recorded_at=1)
    first = advance_loop(current, LoopEvent.PREPARED, LoopPolicy())
    store.append_result(run_id="run-stale", expected_version=0, result=first, recorded_at=2)

    with pytest.raises(LoopStateConflictError, match="version conflict"):
        store.append_result(
            run_id="run-stale",
            expected_version=0,
            result=first,
            recorded_at=3,
        )

    assert store.load_run("run-stale").version == 1
    assert len(store.list_events("run-stale")) == 1


def test_previous_context_mismatch_is_rejected_atomically(tmp_path: Path) -> None:
    store = _store(tmp_path)
    current = LoopContext()
    store.create_run(run_id="run-mismatch", task_id="task-mismatch", context=current, recorded_at=1)
    different = LoopContext(state=LoopState.READY)
    result = advance_loop(different, LoopEvent.STARTED, LoopPolicy())

    with pytest.raises(LoopStateConflictError, match="previous context mismatch"):
        store.append_result(
            run_id="run-mismatch",
            expected_version=0,
            result=result,
            recorded_at=2,
        )

    assert store.load_run("run-mismatch").context == current
    assert store.list_events("run-mismatch") == []


def test_tampered_current_context_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "loop-state.sqlite"
    store = LoopStateStore(path)
    store.initialize()
    store.create_run(run_id="run-tamper", task_id="task-tamper", context=LoopContext(), recorded_at=1)

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE loop_runs SET context_json = ? WHERE run_id = ?",
            ('{"state":"DONE"}', "run-tamper"),
        )
        connection.commit()

    with pytest.raises(LoopStateCorruptionError, match="hash mismatch"):
        store.load_run("run-tamper")


def test_tampered_event_context_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "loop-state.sqlite"
    store = LoopStateStore(path)
    store.initialize()
    current = LoopContext()
    store.create_run(run_id="run-event-tamper", task_id="task-event-tamper", context=current, recorded_at=1)
    result = advance_loop(current, LoopEvent.PREPARED, LoopPolicy())
    store.append_result(
        run_id="run-event-tamper",
        expected_version=0,
        result=result,
        recorded_at=2,
    )

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE loop_events SET current_context_hash = ? WHERE run_id = ?",
            ("0" * 64, "run-event-tamper"),
        )
        connection.commit()

    with pytest.raises(LoopStateCorruptionError, match="hash mismatch"):
        store.list_events("run-event-tamper")


def test_duplicate_run_and_unsafe_tokens_fail_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_run(run_id="run-duplicate", task_id="task-duplicate", context=LoopContext(), recorded_at=1)

    with pytest.raises(LoopStateConflictError, match="already exists"):
        store.create_run(
            run_id="run-duplicate",
            task_id="task-duplicate",
            context=LoopContext(),
            recorded_at=2,
        )
    with pytest.raises(ValueError):
        store.create_run(
            run_id="../private",
            task_id="task",
            context=LoopContext(),
            recorded_at=1,
        )


def test_event_versions_are_monotonic(tmp_path: Path) -> None:
    store = _store(tmp_path)
    current = LoopContext()
    store.create_run(run_id="run-order", task_id="task-order", context=current, recorded_at=1)
    first = advance_loop(current, LoopEvent.PREPARED, LoopPolicy())
    stored = store.append_result(run_id="run-order", expected_version=0, result=first, recorded_at=2)
    second = advance_loop(stored.context, LoopEvent.STARTED, LoopPolicy())
    store.append_result(run_id="run-order", expected_version=1, result=second, recorded_at=3)

    assert [event.version for event in store.list_events("run-order")] == [1, 2]


def test_unsupported_schema_version_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "loop-state.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 99")
        connection.commit()

    with pytest.raises(LoopStateCorruptionError, match="schema version"):
        LoopStateStore(path).initialize()
