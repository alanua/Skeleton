from __future__ import annotations

from datetime import date
import re

import pytest

from core.operator_overview import (
    NO_RELIABLE_PROGRESS,
    UNCERTAIN_LABEL,
    AcceptanceGate,
    OperatorOverviewSource,
    build_operator_overview,
    load_operator_overview,
    render_operator_overview_mobile_html,
)


def test_synthetic_explicit_gates_compute_explainable_progress() -> None:
    overview = build_operator_overview(
        [
            OperatorOverviewSource(
                source_id="capability:synthetic_gate_demo",
                source_path="tests/fixture.yaml",
                status="available",
                summary="Synthetic gate demo.",
                group="core/security",
                acceptance_gates=(
                    AcceptanceGate("a", True, "A"),
                    AcceptanceGate("b", True, "B"),
                    AcceptanceGate("c", False, "C"),
                ),
            )
        ],
        today=date(2026, 8, 12),
    )

    item = overview.groups[0].items[0]
    assert item.progress.percent == 67
    assert item.progress.label == "67%"
    assert item.progress.explanation == "2/3 явних gates виконано."


def test_unmeasurable_progress_has_no_fake_percentage() -> None:
    overview = build_operator_overview(
        [
            OperatorOverviewSource(
                source_id="capability:no_gate_demo",
                source_path="tests/fixture.yaml",
                status="planned",
                summary="No gates.",
                group="interfaces/control",
            )
        ],
        today=date(2026, 8, 12),
    )

    item = overview.groups[-1].items[0]
    assert item.progress.percent is None
    assert item.progress.label == NO_RELIABLE_PROGRESS


def test_stale_and_conflicting_state_is_visibly_uncertain() -> None:
    overview = build_operator_overview(
        [
            OperatorOverviewSource(
                source_id="capability:stale_conflict",
                source_path="old.yaml",
                status="available",
                summary="Old status.",
                group="core/security",
                last_verified=date(2026, 5, 1),
            ),
            OperatorOverviewSource(
                source_id="capability:stale_conflict",
                source_path="new.yaml",
                status="planned",
                summary="New status.",
                group="core/security",
            ),
        ],
        today=date(2026, 8, 12),
    )

    item = overview.groups[0].items[0]
    assert item.needs_verification is True
    assert item.status_label == UNCERTAIN_LABEL
    assert item.blocker_plain == UNCERTAIN_LABEL


def test_retained_capability_portfolio_keeps_entries_once() -> None:
    overview = build_operator_overview(
        [
            OperatorOverviewSource(
                source_id="capability:retained_demo",
                source_path="capabilities.yaml",
                status="available",
                summary="Retained capability.",
                group="core/security",
            ),
            OperatorOverviewSource(
                source_id="capability:superseded_demo",
                source_path="ideas.yaml",
                status="rejected",
                summary="Superseded idea.",
                group="interfaces/control",
                retained=True,
                supersession_reason="Замінено новішою архітектурою.",
            ),
        ],
        today=date(2026, 8, 12),
    )

    ids = [item.item_id for item in overview.retained_portfolio]
    assert ids.count("capability:retained_demo") == 1
    superseded = next(item for item in overview.retained_portfolio if item.item_id == "capability:superseded_demo")
    assert superseded.supersession_reason == "Замінено новішою архітектурою."


def test_mobile_html_keeps_raw_refs_in_drilldown_only() -> None:
    overview = build_operator_overview(
        [
            OperatorOverviewSource(
                source_id="capability:github_task_queue",
                source_path="CAPABILITY_REGISTRY.yaml",
                status="available",
                summary="Queue landed after PR #862 on b8429935c8af51bf3d366155ca1d33df153f69e0.",
                group="autonomy/self-healing",
                current_focus="Issue #2462 status is public-safe.",
                next_milestone="Validate current branch.",
                raw_refs=("#862", "b8429935c8af51bf3d366155ca1d33df153f69e0"),
            )
        ],
        today=date(2026, 8, 12),
    )

    html = render_operator_overview_mobile_html(overview)
    primary_html = re.sub(r"<details>.*?</details>", "", html, flags=re.S)
    assert "технічний ref" in primary_html
    assert "#862" not in primary_html
    assert "b8429935c8af51bf3d366155ca1d33df153f69e0" not in primary_html
    assert "#862" in html
    assert "b8429935c8af51bf3d366155ca1d33df153f69e0" in html
    assert 'name="viewport"' in html
    assert "@media (max-width: 480px)" in html


def test_load_current_repo_has_required_top_groups_and_retained_portfolio() -> None:
    overview = load_operator_overview(".", today=date(2026, 8, 12))
    assert [group.group_id for group in overview.groups] == [
        "core/security",
        "autonomy/self-healing",
        "memory/knowledge",
        "mail/documents/calendar",
        "Home/Home Edge",
        "AI executors",
        "domains/projects",
        "interfaces/control",
    ]
    assert overview.retained_portfolio
    assert any(item.item_id == "capability:boot_manifest" for item in overview.retained_portfolio)


def test_retained_duplicate_without_supersession_is_rejected() -> None:
    from core.operator_overview import OperatorOverviewItem, OperatorOverviewStatus, ProgressView, _validate_retained_portfolio

    item = OperatorOverviewItem(
        item_id="duplicate",
        group="core/security",
        human_name="Duplicate",
        purpose="Test",
        status=OperatorOverviewStatus.LIVE,
        status_label="Працює",
        current_focus="Test",
        next_milestone="Test",
        blocker_plain="Явного блокера немає.",
        progress=ProgressView(NO_RELIABLE_PROGRESS, None, "Test"),
        source_paths=("a.yaml",),
    )
    with pytest.raises(ValueError, match="retained_portfolio_duplicate"):
        _validate_retained_portfolio((item, item))
