from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

from core.telegram_approval_buttons import CALLBACK_SCHEMA
from core.telegram_operator_event_bridge import (
    BRIDGE_SCHEMA,
    bridge_callback_to_operator_event,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "telegram_operator_event_bridge.schema.json"
HEAD_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f901234abcd"
NEXT_HEAD_SHA = "0f0e0d0c0b0a99887766554433221100ffeeddcc"
EXPECTED_FILES = ("core/telegram_operator_event_bridge.py",)


def callback(action: str, **overrides: object) -> dict[str, object]:
    values = {
        "schema": CALLBACK_SCHEMA,
        "action": action,
        "repo": "alanua/Skeleton",
        "pr_number": 123,
        "head_sha": HEAD_SHA,
    }
    values.update(overrides)
    return values


def bridge(callback_payload: object, **overrides: object):
    values = {
        "repo": "alanua/Skeleton",
        "issue_number": 456,
        "pr_number": 123,
        "current_head_sha": HEAD_SHA,
        "expected_files": EXPECTED_FILES,
        "actor_reference": "telegram:user-42",
        "timestamp": "2026-05-21T12:34:56Z",
    }
    values.update(overrides)
    return bridge_callback_to_operator_event(callback_payload, **values)


def test_valid_approve_callback_produces_event_and_issue_comment_text() -> None:
    result = bridge(callback("approve"))

    assert result.status == "validated"
    assert result.reasons == ()
    assert result.event["action_name"] == "telegram_approve"
    assert result.event["result"] == "validated"
    assert result.event["source"] == "telegram_callback"
    assert "Operator event record (stage 1 dry run)" in result.issue_comment_text
    assert "Action: telegram_approve" in result.issue_comment_text
    assert "Result: validated" in result.issue_comment_text


@pytest.mark.parametrize("action", ("reject", "details", "open_pr"))
def test_valid_non_approve_callbacks_produce_dry_run_event_records(action: str) -> None:
    result = bridge(callback(action))

    assert result.status == "dry_run"
    assert result.event["action_name"] == f"telegram_{action}"
    assert result.event["result"] == "dry_run"
    assert result.reasons == ()
    assert "no live action was executed" in result.event["summary"]


def test_stale_head_callback_is_blocked_and_rendered() -> None:
    result = bridge(callback("approve"), current_head_sha=NEXT_HEAD_SHA)

    assert result.status == "blocked"
    assert result.reasons == ("callback head_sha is stale.",)
    assert result.event["head_sha"] == NEXT_HEAD_SHA
    assert result.event["result"] == "blocked"
    assert "Result: blocked" in result.issue_comment_text


def test_malformed_callback_is_blocked() -> None:
    malformed = callback("approve")
    malformed.pop("head_sha")

    result = bridge(malformed)

    assert result.status == "blocked"
    assert result.event["action_name"] == "telegram_approve"
    assert "callback payload fields are malformed." in result.reasons
    assert "callback head_sha must be a 40-character Git SHA." in result.reasons


def test_event_is_bound_to_supplied_pr_state_actor_and_timestamp() -> None:
    result = bridge(callback("details"))
    event = result.event

    assert event["repo"] == "alanua/Skeleton"
    assert event["issue_number"] == 456
    assert event["pr_number"] == 123
    assert event["head_sha"] == HEAD_SHA
    assert event["action_name"] == "telegram_details"
    assert event["actor_reference"] == "telegram:user-42"
    assert event["timestamp"] == "2026-05-21T12:34:56Z"
    assert "Repository: alanua/Skeleton" in result.issue_comment_text
    assert "Issue: #456" in result.issue_comment_text
    assert "Pull request: #123" in result.issue_comment_text


def test_bridge_result_is_deterministic() -> None:
    first = bridge(callback("approve"))
    second = bridge(callback("approve"))

    assert first == second
    assert first.to_dict() == {
        "schema": BRIDGE_SCHEMA,
        "status": "validated",
        "event": first.event,
        "issue_comment_text": first.issue_comment_text,
        "reasons": [],
    }


def test_callback_pr_state_mismatch_is_blocked() -> None:
    result = bridge(callback("details", pr_number=321))

    assert result.status == "blocked"
    assert result.event["pr_number"] == 123
    assert result.reasons == ("callback pr_number does not match current PR state.",)


def test_no_network_or_subprocess_usage_with_mocks() -> None:
    with mock.patch.object(subprocess, "run") as run, mock.patch.object(
        urllib.request, "urlopen"
    ) as urlopen:
        result = bridge(callback("approve"))

    assert result.status == "validated"
    run.assert_not_called()
    urlopen.assert_not_called()


def test_schema_documents_bridge_result_shape() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$id"] == "skeleton.telegram_operator_event_bridge.schema.json"
    assert schema["properties"]["schema"]["const"] == BRIDGE_SCHEMA
    assert schema["required"] == [
        "schema",
        "status",
        "event",
        "issue_comment_text",
        "reasons",
    ]
