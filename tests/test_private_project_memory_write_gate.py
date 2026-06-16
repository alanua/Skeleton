from __future__ import annotations

import json

import pytest

from core.private_project_memory import (
    PROJECT_MEMORY_STATUS_SCHEMA,
    summarize_project_memory_registry,
)


def synthetic_record(
    project_ref: str = "synthetic-001",
    *,
    state: str = "active",
    attention: str = "none",
    schema_ready: bool = True,
    stale: bool = False,
    task_backlog_count: int = 0,
    open_decision_count: int = 0,
) -> dict[str, object]:
    return {
        "schema": PROJECT_MEMORY_STATUS_SCHEMA,
        "project_ref": project_ref,
        "state": state,
        "attention": attention,
        "schema_ready": schema_ready,
        "stale": stale,
        "task_backlog_count": task_backlog_count,
        "open_decision_count": open_decision_count,
    }


def assert_blocked_without_registry_record(report: dict[str, object]) -> None:
    serialized = json.dumps(report, sort_keys=True).lower()

    assert report["status"] == "BLOCKED"
    assert report["project_count"] == 0
    assert report["schema_ready_count"] == 0
    assert report["stale_project_count"] == 0
    assert report["blocked_project_count"] == 0
    assert report["total_task_backlog_count"] == 0
    assert report["total_open_decision_count"] == 0
    assert report["error_class"] == "PrivateProjectMemoryPrivacyError"
    assert report["next_operator_action"] == "configure_project_memory_registry"
    assert "synthetic-write-request" not in serialized
    assert "insert into" not in serialized
    assert "registry.local.json" not in serialized
    assert "write_mode" not in serialized


@pytest.mark.parametrize(
    "records",
    [
        [
            {
                **synthetic_record("synthetic-write-request"),
                "write_mode": True,
            }
        ],
        [
            {
                **synthetic_record("synthetic-001"),
                "operation": "INSERT INTO private_project_memory VALUES (:record)",
            }
        ],
        [
            {
                **synthetic_record("synthetic-001"),
                "registry_record": synthetic_record("synthetic-002"),
            }
        ],
        [
            {
                **synthetic_record("synthetic-001"),
                "destination": "registry.local.json",
            }
        ],
    ],
)
def test_write_shaped_project_memory_input_fails_closed(records: list[dict[str, object]]) -> None:
    report = summarize_project_memory_registry(records)

    assert_blocked_without_registry_record(report)


def test_write_gate_runs_before_unsafe_input_can_become_public_counts() -> None:
    report = summarize_project_memory_registry(
        [
            {
                **synthetic_record("synthetic-write-request"),
                "write_payload": {
                    "state": "active",
                    "task_backlog_count": 99,
                    "open_decision_count": 88,
                },
            }
        ]
    )

    assert_blocked_without_registry_record(report)


def test_write_gate_checks_iterable_records_before_aggregation() -> None:
    records = (
        {
            **synthetic_record("synthetic-001"),
            "operation": "UPDATE private_project_memory SET state = :state",
        }
        for _ in range(1)
    )

    report = summarize_project_memory_registry(records)

    assert_blocked_without_registry_record(report)


def test_safe_synthetic_aggregate_records_still_summarize_after_write_gate() -> None:
    report = summarize_project_memory_registry(
        [
            synthetic_record(
                "synthetic-001",
                task_backlog_count=2,
                open_decision_count=1,
            ),
            synthetic_record(
                "synthetic-002",
                state="paused",
                attention="operator",
                task_backlog_count=3,
            ),
        ]
    )

    assert report["status"] == "DONE"
    assert report["project_count"] == 2
    assert report["state_counts"]["active"] == 1
    assert report["state_counts"]["paused"] == 1
    assert report["attention_counts"]["operator"] == 1
    assert report["schema_ready_count"] == 2
    assert report["total_task_backlog_count"] == 5
    assert report["total_open_decision_count"] == 1
    assert report["error_class"] is None
