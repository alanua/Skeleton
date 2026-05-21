from __future__ import annotations

import json
from pathlib import Path

from core.action_gate import ActionGateRequest, validate_action_request


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "action_gate.schema.json"
HEAD_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f901234abcd"


def valid_request(**overrides: object) -> ActionGateRequest:
    values = {
        "action_type": "merge_pull_request",
        "repo": "alanua/Skeleton",
        "pr_number": 123,
        "expected_head_sha": HEAD_SHA,
        "expected_files": ("core/action_gate.py", "tests/test_action_gate.py"),
        "user_approved": True,
    }
    values.update(overrides)
    return ActionGateRequest(**values)


def test_allows_approved_request_bound_to_expected_pr_state() -> None:
    decision = validate_action_request(valid_request())

    assert decision.status == "allowed"
    assert decision.action_type == "merge_pull_request"
    assert decision.repo == "alanua/Skeleton"
    assert decision.pr_number == 123
    assert decision.reasons == ()


def test_blocks_unapproved_request() -> None:
    decision = validate_action_request(valid_request(user_approved=False))

    assert decision.status == "blocked"
    assert decision.reasons == ("user_approved must be true.",)


def test_blocks_invalid_action_repo_pr_sha_and_file_list() -> None:
    decision = validate_action_request(
        valid_request(
            action_type="deploy",
            repo="alanua/Other",
            pr_number=0,
            expected_head_sha="not-a-sha",
            expected_files=("../outside.py",),
        )
    )

    assert decision.status == "blocked"
    assert decision.pr_number is None
    assert decision.reasons == (
        "action_type is not allowlisted.",
        "repo is not allowlisted.",
        "pr_number must be a positive integer.",
        "expected_head_sha must be a 40-character Git SHA.",
        "expected_files must contain safe repository-relative paths.",
    )


def test_blocks_empty_and_duplicate_expected_files() -> None:
    empty = validate_action_request(valid_request(expected_files=()))
    duplicate = validate_action_request(
        valid_request(expected_files=("docs/ACTION_GATE.md", "docs/ACTION_GATE.md"))
    )

    assert empty.reasons == ("expected_files must be a non-empty tuple of repository-relative paths.",)
    assert duplicate.reasons == ("expected_files must not contain duplicates.",)


def test_schema_documents_stage_1_request_shape() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$id"] == "skeleton.action_gate.schema.json"
    assert schema["required"] == [
        "action_type",
        "repo",
        "pr_number",
        "expected_head_sha",
        "expected_files",
        "user_approved",
    ]
    assert schema["properties"]["action_type"]["enum"] == ["merge_pull_request"]
    assert schema["properties"]["repo"]["enum"] == ["alanua/Skeleton"]
    assert schema["properties"]["user_approved"]["const"] is True
