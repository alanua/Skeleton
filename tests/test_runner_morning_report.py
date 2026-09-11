from __future__ import annotations

import pytest

from core.runner_morning_report import (
    RUNNER_MORNING_REPORT_SCHEMA,
    build_runner_morning_report,
    render_runner_morning_report,
    render_runner_morning_report_from_receipts,
)


def test_runner_morning_report_groups_public_safe_receipts_in_ukrainian() -> None:
    tasks = [
        {
            "schema": "synthetic.runner_task_receipt.v1",
            "public_safe": True,
            "task_id": "3559",
            "title": "Fix retry policy docs",
            "status": "blocked",
            "reason": "validation_failed",
            "next_step": "Re-run focused pytest after fixture repair.",
        },
        {
            "schema": "synthetic.runner_task_receipt.v1",
            "public_safe": True,
            "task_id": "3557",
            "title": "Morning report aggregator",
            "status": "done",
        },
        {
            "schema": "synthetic.runner_task_receipt.v1",
            "public_safe": True,
            "task_id": "3560",
            "title": "Protected maintenance route",
            "status": "needs_operator_approval",
            "approval_reason": "protected_route",
        },
    ]
    prs = [
        {
            "schema": "synthetic.runner_pr_receipt.v1",
            "public_safe": True,
            "pr_number": 42,
            "title": "Runner queue cleanup",
            "status": "ready_for_review",
        }
    ]

    report = build_runner_morning_report(tasks, prs)
    text = render_runner_morning_report(report)

    assert report.schema == RUNNER_MORNING_REPORT_SCHEMA
    assert "Ранковий звіт Runner" in text
    assert "Виконано:\n- task:3557: Morning report aggregator" in text
    assert "Готово до review:\n- PR #42: Runner queue cleanup" in text
    assert "Заблоковано з причиною:\n- task:3559: Fix retry policy docs - причина: validation_failed" in text
    assert "Потребує approval оператора:\n- task:3560: Protected maintenance route - причина: protected_route" in text
    assert "Наступна продуктивна робота:\n- Оператору перевірити approval для task:3560." in text
    assert "private" not in text.lower()
    assert "evidence" not in text.lower()


def test_runner_morning_report_is_deterministic_for_unordered_input() -> None:
    tasks = [
        {"public_safe": True, "task_id": "12", "title": "Second done", "status": "done"},
        {"public_safe": True, "task_id": "2", "title": "First done", "status": "done"},
    ]
    prs = [
        {"public_safe": True, "pr_number": 9, "title": "Later PR", "status": "ready_for_review"},
        {"public_safe": True, "pr_number": 3, "title": "Earlier PR", "status": "ready_for_review"},
    ]

    first = render_runner_morning_report_from_receipts(tasks, prs)
    second = render_runner_morning_report_from_receipts(list(reversed(tasks)), list(reversed(prs)))

    assert first == second
    assert first.index("task:2") < first.index("task:12")
    assert first.index("PR #3") < first.index("PR #9")


def test_runner_morning_report_defaults_empty_sections_and_next_work() -> None:
    text = render_runner_morning_report_from_receipts([], [])

    assert "Виконано:\n- Немає." in text
    assert "Готово до review:\n- Немає." in text
    assert "Заблоковано з причиною:\n- Немає." in text
    assert "Потребує approval оператора:\n- Немає." in text
    assert "Взяти наступну public-safe задачу з runner:ready." in text


def test_runner_morning_report_accepts_ukrainian_public_safe_metadata() -> None:
    text = render_runner_morning_report_from_receipts(
        [
            {
                "public_safe": True,
                "no_private_evidence": True,
                "task_id": "77",
                "title": "Український короткий підсумок",
                "status": "done",
            }
        ],
        [],
    )

    assert "task:77: Український короткий підсумок" in text


def test_runner_morning_report_rejects_receipts_without_public_safe_marker() -> None:
    with pytest.raises(ValueError, match="public_safe=true"):
        build_runner_morning_report([{"task_id": "1", "title": "Missing marker", "status": "done"}], [])


def test_runner_morning_report_rejects_private_evidence_and_raw_paths() -> None:
    with pytest.raises(ValueError, match="not allowed"):
        build_runner_morning_report(
            [
                {
                    "public_safe": True,
                    "task_id": "1",
                    "title": "Has evidence",
                    "status": "done",
                    "raw_evidence": "synthetic evidence",
                }
            ],
            [],
        )

    with pytest.raises(ValueError, match="raw private path"):
        build_runner_morning_report(
            [
                {
                    "public_safe": True,
                    "task_id": "2",
                    "title": "Leaks path",
                    "status": "blocked",
                    "reason": "/home/agent/private/out.txt",
                }
            ],
            [],
        )


def test_runner_morning_report_rejects_malformed_pr_receipt() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        build_runner_morning_report([], [{"public_safe": True, "pr_number": "7", "title": "Bad PR", "status": "open"}])
