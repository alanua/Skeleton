from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path
from unittest import mock

from core.telegram_approval_buttons import (
    CALLBACK_SCHEMA,
    build_pr_ready_card_payload,
    validate_callback_payload,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "telegram_approval_buttons.schema.json"
HEAD_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f901234abcd"
NEXT_HEAD_SHA = "0f0e0d0c0b0a99887766554433221100ffeeddcc"
EXPECTED_FILES = (
    "core/telegram_approval_buttons.py",
    "tests/test_telegram_approval_buttons.py",
)


def card_payload(**overrides: object) -> dict[str, object]:
    values = {
        "repo": "alanua/Skeleton",
        "pr_number": 123,
        "head_sha": HEAD_SHA,
        "changed_files": EXPECTED_FILES,
        "test_summary": "python3 -m pytest -q passed.",
        "risk_summary": "Stage 1 validates callbacks only.",
        "pr_url": "https://github.com/alanua/Skeleton/pull/123",
    }
    values.update(overrides)
    return build_pr_ready_card_payload(**values)


def callback(action: str) -> dict[str, object]:
    button = next(button for button in card_payload()["buttons"] if button["action"] == action)
    return button["callback_payload"]


def test_builds_deterministic_pr_card_payload() -> None:
    payload = card_payload(
        changed_files=(
            "tests/test_telegram_approval_buttons.py",
            "core/telegram_approval_buttons.py",
        ),
        test_summary="python3 -m pytest -q\npassed.",
    )

    assert payload == card_payload()
    assert payload["schema"] == "skeleton.telegram_approval_buttons.card.v1"
    assert payload["changed_files"] == list(EXPECTED_FILES)
    assert "PR ready for operator review" in payload["text"]
    assert "Tests: python3 -m pytest -q passed." in payload["text"]


def test_includes_expected_button_actions() -> None:
    payload = card_payload()

    assert [button["action"] for button in payload["buttons"]] == [
        "approve",
        "reject",
        "details",
        "open_pr",
    ]
    assert payload["buttons"][-1]["url"] == "https://github.com/alanua/Skeleton/pull/123"


def test_approve_action_carries_repo_pr_number_and_head_sha() -> None:
    assert callback("approve") == {
        "schema": CALLBACK_SCHEMA,
        "repo": "alanua/Skeleton",
        "pr_number": 123,
        "head_sha": HEAD_SHA,
        "action": "approve",
    }


def test_reject_action_carries_repo_pr_number_and_head_sha() -> None:
    assert callback("reject") == {
        "schema": CALLBACK_SCHEMA,
        "repo": "alanua/Skeleton",
        "pr_number": 123,
        "head_sha": HEAD_SHA,
        "action": "reject",
    }


def test_details_and_open_pr_do_not_enter_action_gate() -> None:
    with mock.patch("core.telegram_approval_buttons.validate_action_request") as gate:
        details = validate_callback_payload(
            callback("details"),
            current_head_sha=HEAD_SHA,
            expected_files=EXPECTED_FILES,
        )
        open_pr = validate_callback_payload(
            callback("open_pr"),
            current_head_sha=HEAD_SHA,
            expected_files=EXPECTED_FILES,
        )

    assert details.status == "validated"
    assert open_pr.status == "validated"
    assert details.action_gate_decision is None
    assert open_pr.action_gate_decision is None
    gate.assert_not_called()


def test_malformed_callback_is_blocked_before_action_gate() -> None:
    malformed = callback("approve")
    malformed.pop("head_sha")
    with mock.patch("core.telegram_approval_buttons.validate_action_request") as gate:
        result = validate_callback_payload(
            malformed,
            current_head_sha=HEAD_SHA,
            expected_files=EXPECTED_FILES,
        )

    assert result.status == "blocked"
    assert "callback payload fields are malformed." in result.reasons
    assert "callback head_sha must be a 40-character Git SHA." in result.reasons
    gate.assert_not_called()


def test_stale_or_wrong_head_callback_is_blocked_before_action_gate() -> None:
    with mock.patch("core.telegram_approval_buttons.validate_action_request") as gate:
        result = validate_callback_payload(
            callback("approve"),
            current_head_sha=NEXT_HEAD_SHA,
            expected_files=EXPECTED_FILES,
        )

    assert result.status == "blocked"
    assert result.reasons == ("callback head_sha is stale.",)
    gate.assert_not_called()


def test_approve_callback_validates_through_action_gate() -> None:
    result = validate_callback_payload(
        callback("approve"),
        current_head_sha=HEAD_SHA,
        expected_files=EXPECTED_FILES,
    )

    assert result.status == "validated"
    assert result.action_gate_decision is not None
    assert result.action_gate_decision.status == "allowed"
    assert result.action_gate_decision.action_type == "merge_pull_request"


def test_no_network_or_subprocess_usage_with_mocks() -> None:
    with mock.patch.object(subprocess, "run") as run, mock.patch.object(
        urllib.request, "urlopen"
    ) as urlopen:
        payload = card_payload()
        result = validate_callback_payload(
            payload["buttons"][0]["callback_payload"],
            current_head_sha=HEAD_SHA,
            expected_files=EXPECTED_FILES,
        )

    assert result.status == "validated"
    run.assert_not_called()
    urlopen.assert_not_called()


def test_schema_documents_card_and_callback_shape() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$id"] == "skeleton.telegram_approval_buttons.schema.json"
    assert schema["properties"]["buttons"]["minItems"] == 4
    assert schema["$defs"]["callback"]["required"] == [
        "schema",
        "action",
        "repo",
        "pr_number",
        "head_sha",
    ]
