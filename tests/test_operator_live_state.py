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


def test_operator_live_state_groups_actual_scheduler_occurrences(tmp_path) -> None:
    store = _store(tmp_path)
    _occurrence(store, "running", "running", "Монтаж працює", 1_000)
    _occurrence(store, "pending", "pending", "Готове завдання", 1_001)
    _occurrence(store, "waiting", "waiting_dependency", "Чекає попередній крок", 1_002)
    _occurrence(store, "attention", "needs_operator", "Потрібне рішення", 1_003)
    _occurrence(store, "failed", "failed", "Помилка", 1_004)
    _occurrence(store, "done", "done", "Завершений крок", 1_005)
    _occurrence(store, "skipped", "skipped", "Пропущений крок", 1_006)

    state = load_operator_live_state(store.db_path, now=1_010)
    sections = state["sections"]

    assert [item["title"] for item in sections["Працює зараз"]] == ["Монтаж працює"]
    assert [item["title"] for item in sections["Чекає"]] == [
        "Чекає попередній крок",
        "Готове завдання",
    ]
    assert [item["title"] for item in sections["Потрібна моя увага"]] == [
        "Помилка",
        "Потрібне рішення",
    ]
    assert [item["title"] for item in sections["Щойно завершено"]] == ["Завершений крок"]
    assert "Пропущений крок" not in str(sections)
    assert state["refreshed_at"] == 1_010
    assert state["status"] == "online"
    assert state["stale"] is False


def test_terminal_items_are_absent_from_active_sections(tmp_path) -> None:
    store = _store(tmp_path)
    _occurrence(store, "done", "done", "Завершено", 2_000)

    sections = load_operator_live_state(store.db_path, now=2_010)["sections"]

    assert sections["Працює зараз"] == []
    assert sections["Чекає"] == []
    assert sections["Потрібна моя увага"] == []
    assert sections["Щойно завершено"][0]["title"] == "Завершено"


def test_primary_items_suppress_technical_refs_until_drilldown(tmp_path) -> None:
    store = _store(tmp_path)
    _occurrence(store, "ref", "running", "runner issue #2677 462b9a1", 3_000)

    state = load_operator_live_state(store.db_path, now=3_010, include_drilldown=True)
    item = state["sections"]["Працює зараз"][0]

    assert item["title"] == "Завдання"
    assert item["drilldown"]["occurrence_id"] == "occ_ref"
    assert "runner" not in item["title"].lower()


def test_stale_state_is_visible_for_old_successful_reads(tmp_path) -> None:
    store = _store(tmp_path)
    _occurrence(store, "old", "running", "Старий стан", 4_000)

    state = read_operator_live_state(store.db_path, now=4_400)

    assert state.stale is True
    assert state.status == "stale"
    assert state.refreshed_at == 4_400


def test_failed_read_returns_offline_without_new_refresh_stamp() -> None:
    state = stale_operator_live_state(refreshed_at=5_000)

    assert state["status"] == "offline"
    assert state["stale"] is True
    assert state["refreshed_at"] == 5_000

    with pytest.raises(Exception):
        read_operator_live_state("/tmp/does-not-exist/scheduler.sqlite3", now=5_100)
