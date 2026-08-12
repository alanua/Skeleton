from __future__ import annotations

from pathlib import Path

import scripts.runner_poll_github_tasks as runner
from core.control_recovery import RecoveryStore


def _done_report() -> str:
    return runner._maintenance_report(
        "DONE",
        runner.REPLENISH_RUNNER_QUEUE,
        ["selected_count=1", "telegram_notifications=0"],
        "met",
    )


def _wire_queue(monkeypatch, tmp_path: Path, *, progress: bool):
    ready: list[dict[str, object]] = []
    candidate = {"number": 990001}
    calls = {"replenish": 0}
    db_path = tmp_path / "control-recovery.sqlite3"

    monkeypatch.setattr(runner, "control_recovery_db_path", lambda: db_path)
    monkeypatch.setattr(runner, "reconcile_scheduler_on_poll", lambda: {})
    monkeypatch.setattr(runner, "get_ready_issues", lambda: list(ready))
    monkeypatch.setattr(
        runner,
        "get_queue_replenisher_candidate_issues",
        lambda: [candidate],
    )
    monkeypatch.setattr(
        runner,
        "select_runner_queue_replenishment_targets",
        lambda ready_issues, candidate_issues: (
            [] if ready_issues else list(candidate_issues)
        ),
    )

    def replenish(_body: str) -> str:
        calls["replenish"] += 1
        if progress and not ready:
            ready.append(candidate)
        return _done_report()

    monkeypatch.setattr(runner, "replenish_runner_queue", replenish)
    return ready, candidate, calls, db_path


def test_poll_once_learns_verified_replenish_and_continues_same_poll(
    monkeypatch, tmp_path: Path
) -> None:
    ready, candidate, calls, db_path = _wire_queue(
        monkeypatch, tmp_path, progress=True
    )
    processed: list[int] = []
    monkeypatch.setattr(
        runner,
        "process_issue",
        lambda issue, workdir=None: processed.append(int(issue["number"])),
    )
    monkeypatch.setattr(
        runner,
        "send_telegram_notification",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("routine recovery must be Telegram-silent")
        ),
    )

    assert runner.poll_once() == 1
    assert calls["replenish"] == 1
    assert processed == [int(candidate["number"])]
    assert ready

    metrics = RecoveryStore(db_path).learning_metrics()
    assert metrics["lessons_verified"] == 1


def test_poll_once_persists_no_progress_and_honors_backoff(
    monkeypatch, tmp_path: Path
) -> None:
    _ready, _candidate, calls, db_path = _wire_queue(
        monkeypatch, tmp_path, progress=False
    )
    monkeypatch.setattr(runner, "process_issue", lambda *_args, **_kwargs: None)

    assert runner.poll_once() == 0
    assert calls["replenish"] == 1
    assert runner.poll_once() == 0
    assert calls["replenish"] == 1

    metrics = RecoveryStore(db_path).learning_metrics()
    assert metrics["lessons_failed"] >= 1


def test_ready_work_never_triggers_replenishment(monkeypatch, tmp_path: Path) -> None:
    ready, candidate, calls, _db_path = _wire_queue(
        monkeypatch, tmp_path, progress=True
    )
    ready.append(candidate)
    processed: list[int] = []
    monkeypatch.setattr(
        runner,
        "process_issue",
        lambda issue, workdir=None: processed.append(int(issue["number"])),
    )

    assert runner.poll_once() == 1
    assert calls["replenish"] == 0
    assert processed == [int(candidate["number"])]


def test_no_eligible_work_does_not_mutate(monkeypatch, tmp_path: Path) -> None:
    _ready, _candidate, calls, _db_path = _wire_queue(
        monkeypatch, tmp_path, progress=True
    )
    monkeypatch.setattr(
        runner,
        "select_runner_queue_replenishment_targets",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(runner, "process_issue", lambda *_args, **_kwargs: None)

    assert runner.poll_once() == 0
    assert calls["replenish"] == 0


def test_verified_lesson_rechecks_current_gates_before_reuse(
    monkeypatch, tmp_path: Path
) -> None:
    ready, candidate, calls, _db_path = _wire_queue(
        monkeypatch, tmp_path, progress=True
    )
    monkeypatch.setattr(runner, "process_issue", lambda *_args, **_kwargs: None)

    assert runner.maybe_recover_idle_runner_queue() is True
    assert calls["replenish"] == 1

    # Current state changed after verification. Reuse must stop at the live gate.
    ready[:] = [candidate]
    assert runner.maybe_recover_idle_runner_queue() is False
    assert calls["replenish"] == 1
