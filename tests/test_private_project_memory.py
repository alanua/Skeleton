from __future__ import annotations

import json
from pathlib import Path

from core.private_project_memory import (
    PROJECT_MEMORY_REGISTRY_SUMMARY_SCHEMA,
    PROJECT_MEMORY_STATUS_SCHEMA,
    summarize_project_memory_registry,
)


def synthetic_record(
    project_ref: str,
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


def assert_public_safe(report: dict[str, object]) -> None:
    serialized = json.dumps(report, sort_keys=True)
    forbidden = (
        "/",
        "\\",
        ".sqlite",
        ".db",
        "github.com",
        "drive.google.com",
        "project_alpha",
        "repo",
        "branch",
        "path",
        "payload",
        "secret",
        "token",
        "task_title",
    )
    for marker in forbidden:
        assert marker not in serialized.lower()


def test_summarizes_synthetic_project_memory_registry() -> None:
    report = summarize_project_memory_registry(
        [
            synthetic_record(
                "synthetic-001",
                state="active",
                attention="none",
                task_backlog_count=3,
                open_decision_count=1,
            ),
            synthetic_record(
                "synthetic-002",
                state="paused",
                attention="review",
                stale=True,
                task_backlog_count=2,
                open_decision_count=4,
            ),
        ]
    )

    assert report["schema"] == PROJECT_MEMORY_REGISTRY_SUMMARY_SCHEMA
    assert report["status"] == "DONE"
    assert report["project_count"] == 2
    assert report["state_counts"] == {
        "active": 1,
        "paused": 1,
        "blocked": 0,
        "archived": 0,
        "unknown": 0,
    }
    assert report["attention_counts"] == {
        "none": 1,
        "review": 1,
        "operator": 0,
        "blocked": 0,
        "unknown": 0,
    }
    assert report["schema_ready_count"] == 2
    assert report["stale_project_count"] == 1
    assert report["blocked_project_count"] == 0
    assert report["total_task_backlog_count"] == 5
    assert report["total_open_decision_count"] == 5
    assert report["error_class"] is None
    assert report["next_operator_action"] == "refresh_stale_project_memory"
    assert_public_safe(report)


def test_empty_registry_reports_configure_action_without_project_records() -> None:
    report = summarize_project_memory_registry([])

    assert report["status"] == "DONE"
    assert report["project_count"] == 0
    assert report["schema_ready_count"] == 0
    assert report["next_operator_action"] == "configure_project_memory_registry"
    assert_public_safe(report)


def test_schema_not_ready_takes_priority_over_attention() -> None:
    report = summarize_project_memory_registry(
        [
            synthetic_record(
                "synthetic-001",
                attention="operator",
                schema_ready=False,
            )
        ]
    )

    assert report["status"] == "DONE"
    assert report["schema_ready_count"] == 0
    assert report["attention_counts"]["operator"] == 1
    assert report["next_operator_action"] == "initialize_project_memory_schema"
    assert_public_safe(report)


def test_blocked_registry_status_takes_priority_after_schema_ready() -> None:
    report = summarize_project_memory_registry(
        [
            synthetic_record(
                "synthetic-001",
                state="blocked",
                attention="blocked",
            )
        ]
    )

    assert report["status"] == "DONE"
    assert report["blocked_project_count"] == 1
    assert report["next_operator_action"] == "review_blocked_project_memory"
    assert_public_safe(report)


def test_registry_blocks_real_local_values_and_zeroes_counts() -> None:
    report = summarize_project_memory_registry(
        [
            {
                **synthetic_record("project_alpha"),
                "path": "/home/user/real/project",
                "repo_url": "https://github.com/example/private",
            }
        ]
    )

    assert report["status"] == "BLOCKED"
    assert report["project_count"] == 0
    assert report["state_counts"]["active"] == 0
    assert report["error_class"] == "PrivateProjectMemoryPrivacyError"
    assert report["next_operator_action"] == "configure_project_memory_registry"
    assert_public_safe(report)


def test_registry_blocks_unsafe_project_reference() -> None:
    report = summarize_project_memory_registry(
        [
            synthetic_record(
                "/home/user/real-project",
                task_backlog_count=1,
            )
        ]
    )

    assert report["status"] == "BLOCKED"
    assert report["project_count"] == 0
    assert report["error_class"] == "PrivateProjectMemoryPrivacyError"
    assert_public_safe(report)


def test_registry_blocks_invalid_counts() -> None:
    report = summarize_project_memory_registry(
        [
            synthetic_record(
                "synthetic-001",
                task_backlog_count=-1,
            )
        ]
    )

    assert report["status"] == "BLOCKED"
    assert report["error_class"] == "PrivateProjectMemoryConfigError"
    assert_public_safe(report)


def test_schema_documents_public_safe_contract() -> None:
    schema_path = Path("schemas/private_project_memory.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    defs = schema["$defs"]
    summary = defs["registry_summary"]
    summary_fields = set(summary["properties"])

    assert "project_ref" not in summary_fields
    assert "records" not in summary_fields
    assert "projects" not in summary_fields
    assert "content" not in json.dumps(summary)
    assert summary["additionalProperties"] is False
    assert defs["project_status"]["additionalProperties"] is False
    assert "schema" in summary["required"]
    assert "next_operator_action" in summary["required"]
