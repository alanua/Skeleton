from __future__ import annotations

import json

import pytest

from core.private_project_memory import (
    PROJECT_MEMORY_STATUS_SCHEMA,
    summarize_project_memory_registry,
)


def synthetic_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "schema": PROJECT_MEMORY_STATUS_SCHEMA,
        "project_ref": "synthetic-001",
        "state": "active",
        "attention": "none",
        "schema_ready": True,
        "stale": False,
        "task_backlog_count": 2,
        "open_decision_count": 1,
    }
    record.update(overrides)
    return record


def assert_public_safe(report: dict[str, object]) -> None:
    serialized = json.dumps(report, sort_keys=True).lower()
    forbidden = (
        "/",
        "\\",
        ".sqlite",
        ".db",
        "github.com",
        "drive.google.com",
        "repo",
        "branch",
        "path",
        "payload",
        "secret",
        "token",
        "task_title",
    )
    for marker in forbidden:
        assert marker not in serialized


@pytest.mark.parametrize("extra_key", ("operation", "action", "actor", "neutral_unknown"))
def test_project_status_write_gate_rejects_unallowed_keys(extra_key: str) -> None:
    report = summarize_project_memory_registry(
        [
            synthetic_record(
                **{
                    extra_key: "synthetic-marker",
                }
            )
        ]
    )

    assert report["status"] == "BLOCKED"
    assert report["project_count"] == 0
    assert report["total_task_backlog_count"] == 0
    assert report["total_open_decision_count"] == 0
    assert report["error_class"] == "PrivateProjectMemoryPrivacyError"
    assert report["next_operator_action"] == "configure_project_memory_registry"
    assert_public_safe(report)


def test_project_status_write_gate_allows_exact_status_shape_before_aggregation() -> None:
    report = summarize_project_memory_registry(
        [
            synthetic_record(),
            synthetic_record(
                project_ref="synthetic-002",
                state="paused",
                attention="review",
                schema_ready=True,
                stale=True,
                task_backlog_count=3,
                open_decision_count=4,
            ),
        ]
    )

    assert report["status"] == "DONE"
    assert report["project_count"] == 2
    assert report["state_counts"]["active"] == 1
    assert report["state_counts"]["paused"] == 1
    assert report["attention_counts"]["review"] == 1
    assert report["schema_ready_count"] == 2
    assert report["stale_project_count"] == 1
    assert report["total_task_backlog_count"] == 5
    assert report["total_open_decision_count"] == 5
    assert report["next_operator_action"] == "refresh_stale_project_memory"
    assert_public_safe(report)
