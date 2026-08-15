from __future__ import annotations

import pytest

from core.operator_live_state import (
    load_operator_live_state,
    read_operator_live_state,
    stale_operator_live_state,
)
from core.scheduler_models import ScheduleSpec, build_execution_proposal
from core.scheduler_store import SchedulerStore


def _schedule(schedule_id: str, title: str) -> ScheduleSpec:
    return ScheduleSpec.from_mapping(
        {
            "schema": "skeleton.schedule.v1",
            "schedule_id": schedule_id,
            "trigger_kind": "once",
            "cron_expression": None,
            "once_at": 100,
            "timezone": "UTC",
            "route_type": "runner",
            "route_id": f"{schedule_id}-route",
            "approval_policy": "auto_run_low_risk",
            "overlap_policy": "queue_one",
            "misfire_policy": "run_once",
            "payload": {
                "operator_title": title,
                "operator_detail": f"{title} деталі",
            },
        }
    )


def _store(tmp_path) -> SchedulerStore:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    return store


def _occurrence(store: SchedulerStore, schedule_id: str, state: str, title: str, now: int):
    schedule, _ = store.register(_schedule(schedule_id, title), now=now)
    proposal = build_execution_proposal(schedule, occurrence_id=f"occ_{schedule_id}", scheduled_for=100)
    return store.create_occurrence(
        occurrence_id=f"occ_{schedule_id}",
        schedule=schedule,
        scheduled_for=100,
        state=state,
        reason=f"{state.upper()}_FIXTURE",
        proposal=proposal,
        now=now,
    )[0]


def _snapshot(now: int, issues: list[dict[str, object]]) -> dict[str, object]:
    return {"generated_at": now, "issues": issues}


def _issue(number: int, title: str, labels: list[str], updated_at: int) -> dict[str, object]:
    return {
        "number": number,
        "title": title,
        "labels": labels,
        "updated_at": updated_at,
    }


def test_operator_live_state_groups_actual_runner_queue_issues(tmp_path) -> None:
    store = _store(tmp_path)
    _occurrence(store, "supplemental", "running", "Не основне джерело", 999)
    snapshot = _snapshot(
        1_009,
        [
            _issue(1, "Монтаж працює", ["runner:running"], 1_000),
            _issue(2, "Готове завдання", ["runner:ready"], 1_001),
            _issue(3, "Чекає попередній крок", ["runner:waiting-dependency"], 1_002),
            _issue(4, "Потрібне рішення", ["runner:blocked"], 1_003),
            _issue(5, "Завершений крок", ["runner:done"], 1_005),
            _issue(6, "Не в черзі", ["agent:task"], 1_006),
        ],
    )

    state = load_operator_live_state(snapshot, store.db_path, now=1_010)
    sections = state["sections"]

    assert [item["title"] for item in sections["Працює зараз"]] == ["Монтаж працює"]
    assert [item["title"] for item in sections["Чекає"]] == [
        "Готове завдання",
        "Чекає попередній крок",
    ]
    assert [item["title"] for item in sections["Потрібна моя увага"]] == ["Потрібне рішення"]
    assert [item["title"] for item in sections["Щойно завершено"]] == ["Завершений крок"]
    assert "Не основне джерело" not in str(sections)
    assert "Не в черзі" not in str(sections)
    assert state["refreshed_at"] == 1_010
    assert state["status"] == "online"
    assert state["stale"] is False


def test_terminal_items_are_absent_from_active_sections(tmp_path) -> None:
    store = _store(tmp_path)
    snapshot = _snapshot(2_009, [_issue(7, "Завершено", ["runner:done"], 2_000)])

    sections = load_operator_live_state(snapshot, store.db_path, now=2_010)["sections"]

    assert sections["Працює зараз"] == []
    assert sections["Чекає"] == []
    assert sections["Потрібна моя увага"] == []
    assert sections["Щойно завершено"][0]["title"] == "Завершено"


def test_primary_items_suppress_technical_refs_until_drilldown(tmp_path) -> None:
    store = _store(tmp_path)
    snapshot = _snapshot(3_009, [_issue(2677, "runner issue #2677 462b9a1", ["runner:running"], 3_000)])

    state = load_operator_live_state(snapshot, store.db_path, now=3_010, include_drilldown=True)
    item = state["sections"]["Працює зараз"][0]

    assert item["title"] == "Завдання"
    assert item["drilldown"]["issue_number"] == "2677"
    assert "runner" not in item["title"].lower()


def test_scheduler_occurrences_are_supplementary_only(tmp_path) -> None:
    store = _store(tmp_path)
    _occurrence(store, "issue-42", "failed", "Scheduler title", 3_500)
    snapshot = _snapshot(3_509, [_issue(42, "Queue title", ["runner:running"], 3_000)])

    state = load_operator_live_state(snapshot, store.db_path, now=3_510, include_drilldown=True)
    item = state["sections"]["Працює зараз"][0]

    assert item["title"] == "Queue title"
    assert item["updated_at"] == 3_500
    assert item["drilldown"]["scheduler_state"] == "failed"
    assert state["sections"]["Потрібна моя увага"] == []


def test_stale_state_is_visible_for_old_successful_reads(tmp_path) -> None:
    store = _store(tmp_path)
    snapshot = _snapshot(4_000, [_issue(8, "Старий стан", ["runner:running"], 4_000)])

    state = read_operator_live_state(snapshot, store.db_path, now=4_400)

    assert state.stale is True
    assert state.status == "stale"
    assert state.refreshed_at == 4_400


def test_failed_read_returns_offline_without_new_refresh_stamp() -> None:
    state = stale_operator_live_state(refreshed_at=5_000)

    assert state["status"] == "offline"
    assert state["stale"] is True
    assert state["refreshed_at"] == 5_000

    with pytest.raises(Exception):
        read_operator_live_state(
            {"generated_at": 5_100, "issues": [{"number": 1}]},
            "/tmp/does-not-exist/scheduler.sqlite3",
            now=5_100,
        )
