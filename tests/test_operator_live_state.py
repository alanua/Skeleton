from __future__ import annotations

from datetime import UTC, datetime
import json
import re

from core.operator_live_state import build_operator_live_state
from core.operator_overview import OperatorOverviewSource


def test_successive_live_snapshots_change_primary_work_without_rebuild() -> None:
    first = build_operator_live_state(
        [
            OperatorOverviewSource(
                source_id="capability:active_work",
                source_path="fixture.yaml",
                status="available",
                summary="Active.",
                group="autonomy/self-healing",
                current_focus="Перевіряє поточний запуск.",
            ),
            OperatorOverviewSource(
                source_id="capability:building_work",
                source_path="fixture.yaml",
                status="building",
                summary="Build.",
                group="interfaces/control",
                current_focus="Збирає перший зріз.",
            ),
        ],
        refreshed_at=datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
    ).as_dict()
    second = build_operator_live_state(
        [
            OperatorOverviewSource(
                source_id="capability:active_work",
                source_path="fixture.yaml",
                status="available",
                summary="Active.",
                group="autonomy/self-healing",
                current_focus="Перевіряє наступний запуск.",
            ),
            OperatorOverviewSource(
                source_id="capability:building_work",
                source_path="fixture.yaml",
                status="building",
                summary="Build.",
                group="interfaces/control",
                current_focus="Збирає live-екран.",
            ),
        ],
        refreshed_at=datetime(2026, 8, 14, 10, 1, tzinfo=UTC),
    ).as_dict()

    by_title_first = {section["title_uk"]: section["rows"] for section in first["sections"]}
    by_title_second = {section["title_uk"]: section["rows"] for section in second["sections"]}

    assert by_title_first["Працює зараз"] != by_title_second["Працює зараз"]
    assert by_title_first["Будується зараз"] != by_title_second["Будується зараз"]
    assert first["refreshed_at"] != second["refreshed_at"]


def test_primary_rows_hide_issue_pr_task_numbers_and_runner_labels() -> None:
    state = build_operator_live_state(
        [
            OperatorOverviewSource(
                source_id="capability:github_task_queue",
                source_path="fixture.yaml",
                status="available",
                summary="Queue.",
                group="autonomy/self-healing",
                current_focus="Issue #2612 and PR #44 on runner/live-sk-dashboard-state are moving.",
            )
        ],
        refreshed_at=datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
    ).as_dict()

    primary = json.dumps(state["sections"], ensure_ascii=False)
    assert not re.search(r"(?i)(issue|pr|task)\s*#?\d+|#\d+|runner/[A-Za-z0-9._/-]+", primary)
    assert "технічне посилання" in primary
    assert "технічна мітка" in primary


def test_stale_projection_keeps_real_freshness_state() -> None:
    state = build_operator_live_state(
        [
            OperatorOverviewSource(
                source_id="capability:waiting_work",
                source_path="fixture.yaml",
                status="waiting",
                summary="Waiting.",
                group="interfaces/control",
            )
        ],
        refreshed_at=datetime(2026, 8, 14, 9, 30, tzinfo=UTC),
        freshness="stale",
    ).as_dict()

    assert state["freshness"] == "stale"
    assert state["refreshed_at"] == "2026-08-14T09:30:00Z"
