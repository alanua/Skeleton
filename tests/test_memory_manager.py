from __future__ import annotations

import builtins
import json
import subprocess
import urllib.request
from pathlib import Path
from unittest import mock

from core.memory_manager import MemoryRecord, classify_memory_record, route_memory_record


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "memory_record.schema.json"


def valid_record(**overrides: object) -> MemoryRecord:
    values = {
        "id": "mem-001",
        "project_id": "skeleton",
        "memory_type": "canon_candidate",
        "source": "current_user_message",
        "trust_level": "runtime_direct",
        "content": "Public routing rule candidate.",
        "status": "active",
        "created_at": "2026-05-20T00:00:00Z",
        "public_safe": True,
        "critique_present": False,
        "operator_approved": False,
        "changes_canon_or_instruction": False,
    }
    values.update(overrides)
    return MemoryRecord(**values)


def test_memory_record_schema_file_exists_and_names_supported_types() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$id"] == "skeleton.memory_record.schema.json"
    assert schema["properties"]["memory_type"]["enum"] == [
        "weak_chat_memory",
        "project_state",
        "canon_candidate",
        "confirmed_canon",
        "private_sensitive",
        "rejected_outdated",
    ]


def test_public_canon_candidate_requires_approval() -> None:
    result = route_memory_record(valid_record(memory_type="canon_candidate", public_safe=True))

    assert result.status == "accepted"
    assert result.target_route == "github_canon_candidate"
    assert result.requires_operator_approval is True
    assert result.blocked_reason is None


def test_private_sensitive_blocks_public_github() -> None:
    result = route_memory_record(valid_record(memory_type="private_sensitive", public_safe=False))

    assert result.status == "blocked"
    assert result.target_route not in {"github_canon_candidate", "github_confirmed_canon"}
    assert result.blocked_reason == "private_sensitive records never route to public GitHub."


def test_confirmed_canon_requires_approval() -> None:
    result = route_memory_record(
        valid_record(memory_type="confirmed_canon", public_safe=True, operator_approved=False)
    )

    assert result.status == "blocked"
    assert result.target_route == "github_confirmed_canon"
    assert result.requires_operator_approval is True
    assert result.blocked_reason == "confirmed_canon requires explicit operator approval."


def test_confirmed_canon_with_approval_routes_to_confirmed_canon() -> None:
    result = route_memory_record(
        valid_record(memory_type="confirmed_canon", public_safe=True, operator_approved=True)
    )

    assert result.status == "accepted"
    assert result.target_route == "github_confirmed_canon"
    assert result.requires_operator_approval is True


def test_weak_chat_memory_routes_to_weak_cache() -> None:
    result = route_memory_record(valid_record(memory_type="weak_chat_memory", public_safe=False))

    assert result.status == "accepted"
    assert result.target_route == "weak_cache"
    assert result.requires_operator_approval is False


def test_rejected_outdated_routes_to_rejected_archive() -> None:
    result = route_memory_record(valid_record(memory_type="rejected_outdated", public_safe=False))

    assert result.status == "accepted"
    assert result.target_route == "rejected_archive"
    assert result.requires_operator_approval is False


def test_instruction_or_canon_change_without_critique_blocks() -> None:
    result = route_memory_record(
        valid_record(
            memory_type="canon_candidate",
            public_safe=True,
            changes_canon_or_instruction=True,
            critique_present=False,
        )
    )

    assert result.status == "blocked"
    assert result.target_route == "github_canon_candidate"
    assert result.blocked_reason == "canon/instruction changes require critique before routing."


def test_chatgpt_memory_is_weak_cache_not_canon() -> None:
    record = valid_record(memory_type="confirmed_canon", source="chatgpt_memory", public_safe=True)

    result = route_memory_record(record)

    assert classify_memory_record(record) == "weak_chat_memory"
    assert result.status == "accepted"
    assert result.target_route == "weak_cache"
    assert "type=weak_chat_memory" in result.audit_summary


def test_dry_run_needs_no_network_subprocess_or_file_write_side_effects() -> None:
    with mock.patch.object(subprocess, "run") as run, mock.patch.object(
        urllib.request, "urlopen"
    ) as urlopen, mock.patch.object(builtins, "open", mock.mock_open()) as opened:
        result = route_memory_record(valid_record(memory_type="weak_chat_memory"))

    assert result.status == "accepted"
    run.assert_not_called()
    urlopen.assert_not_called()
    opened.assert_not_called()
