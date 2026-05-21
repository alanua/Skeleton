from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

from core.operator_event import (
    EVENT_SCHEMA,
    MAX_SUMMARY_CHARS,
    OperatorEvent,
    operator_event_to_dict,
    render_public_issue_comment,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "operator_event.schema.json"
HEAD_SHA = "A1B2C3D4E5F60718293A4B5C6D7E8F901234ABCD"
NORMALIZED_HEAD_SHA = HEAD_SHA.lower()


def valid_event(**overrides: object) -> OperatorEvent:
    values = {
        "repo": "alanua/Skeleton",
        "issue_number": 456,
        "pr_number": 123,
        "head_sha": HEAD_SHA,
        "event_type": "operator_console_interaction",
        "action_name": "approve_pr",
        "result": "validated",
        "source": "operator_console",
        "actor_reference": "github:@oleksii",
        "timestamp": "2026-05-21T12:34:56Z",
        "summary": "Operator validated the reviewed pull request.",
    }
    values.update(overrides)
    return OperatorEvent(**values)


def test_event_dict_is_deterministic() -> None:
    event = valid_event(summary="Operator validated\n the reviewed pull request.")

    assert operator_event_to_dict(event) == valid_event().to_dict()
    assert operator_event_to_dict(event) == {
        "schema": EVENT_SCHEMA,
        "repo": "alanua/Skeleton",
        "issue_number": 456,
        "pr_number": 123,
        "head_sha": NORMALIZED_HEAD_SHA,
        "event_type": "operator_console_interaction",
        "action_name": "approve_pr",
        "result": "validated",
        "source": "operator_console",
        "actor_reference": "github:@oleksii",
        "timestamp": "2026-05-21T12:34:56Z",
        "summary": "Operator validated the reviewed pull request.",
    }


def test_comment_text_is_deterministic() -> None:
    assert render_public_issue_comment(valid_event()) == "\n".join(
        (
            "Operator event record (stage 1 dry run)",
            "Repository: alanua/Skeleton",
            "Issue: #456",
            "Pull request: #123",
            f"Head SHA: {NORMALIZED_HEAD_SHA}",
            "Event type: operator_console_interaction",
            "Action: approve_pr",
            "Result: validated",
            "Source: operator_console",
            "Actor reference: github:@oleksii",
            "Timestamp: 2026-05-21T12:34:56Z",
            "Summary: Operator validated the reviewed pull request.",
        )
    )
    assert valid_event().render_public_issue_comment() == render_public_issue_comment(valid_event())


def test_event_is_bound_to_repo_issue_pr_head_sha_and_action_name() -> None:
    record = valid_event().to_dict()
    comment = render_public_issue_comment(valid_event())

    assert record["repo"] == "alanua/Skeleton"
    assert record["issue_number"] == 456
    assert record["pr_number"] == 123
    assert record["head_sha"] == NORMALIZED_HEAD_SHA
    assert record["action_name"] == "approve_pr"
    assert "Issue: #456" in comment
    assert "Pull request: #123" in comment
    assert f"Head SHA: {NORMALIZED_HEAD_SHA}" in comment
    assert "Action: approve_pr" in comment


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("repo", "alanua/private"),
        ("issue_number", 0),
        ("pr_number", True),
        ("head_sha", "not-a-sha"),
        ("event_type", "merge"),
        ("action_name", "Approve PR"),
        ("result", "merged"),
        ("source", "webhook"),
        ("actor_reference", "Oleksii"),
        ("timestamp", "2026-05-21T12:34:56+00:00"),
        ("summary", ""),
    ),
)
def test_malformed_events_are_blocked(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        valid_event(**{field: value})


def test_bounded_summary_enforced() -> None:
    with pytest.raises(ValueError, match="summary must be at most"):
        valid_event(summary="x" * (MAX_SUMMARY_CHARS + 1))


def test_no_network_or_subprocess_usage_with_mocks() -> None:
    with mock.patch.object(subprocess, "run") as run, mock.patch.object(
        urllib.request, "urlopen"
    ) as urlopen:
        record = valid_event().to_dict()
        comment = render_public_issue_comment(valid_event())

    assert record["result"] == "validated"
    assert "Operator event record" in comment
    run.assert_not_called()
    urlopen.assert_not_called()


def test_schema_documents_bounded_event_shape() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$id"] == "skeleton.operator_event.schema.json"
    assert schema["required"] == [
        "schema",
        "repo",
        "issue_number",
        "pr_number",
        "head_sha",
        "event_type",
        "action_name",
        "result",
        "source",
        "actor_reference",
        "timestamp",
        "summary",
    ]
    assert schema["properties"]["summary"]["maxLength"] == MAX_SUMMARY_CHARS
