from __future__ import annotations

import importlib
import json
from pathlib import Path
import subprocess
import urllib.request
from unittest import mock

import pytest

from core.event_ledger import (
    ALLOWED_EVENT_STATUSES,
    EVENT_LEDGER_SCHEMA,
    MAX_ATTRIBUTE_KEYS,
    MAX_ATTRIBUTE_STRING_CHARS,
    MAX_SUMMARY_CHARS,
    EventLedgerEvent,
    event_ledger_event_to_dict,
    event_ledger_event_to_json,
    validate_identifier,
)


def valid_event(**overrides: object) -> EventLedgerEvent:
    values = {
        "workflow_id": "runner_bridge",
        "event_id": "runner_bridge.issue_263",
        "event_type": "task.validated",
        "status": "completed",
        "actor_reference": "github:@oleksii",
        "timestamp": "2026-05-22T08:30:00Z",
        "summary": "Runner validated event ledger stage 1.",
        "attributes": {
            "base_ref": "main",
            "issue_number": 263,
            "validation": {
                "command": "python3 -m pytest -q",
                "passed": True,
            },
        },
    }
    values.update(overrides)
    return EventLedgerEvent(**values)


def test_event_dict_uses_fixed_public_safe_shape() -> None:
    event = valid_event(summary="Runner validated\n event ledger stage 1.")

    assert event_ledger_event_to_dict(event) == {
        "schema": EVENT_LEDGER_SCHEMA,
        "workflow_id": "runner_bridge",
        "event_id": "runner_bridge.issue_263",
        "event_type": "task.validated",
        "status": "completed",
        "actor_reference": "github:@oleksii",
        "timestamp": "2026-05-22T08:30:00Z",
        "summary": "Runner validated event ledger stage 1.",
        "attributes": {
            "base_ref": "main",
            "issue_number": 263,
            "validation": {
                "command": "python3 -m pytest -q",
                "passed": True,
            },
        },
    }
    assert valid_event().to_dict() == event_ledger_event_to_dict(valid_event())


def test_json_serialization_is_compact_and_deterministic() -> None:
    first = valid_event(
        attributes={
            "validation": {"passed": True, "command": "python3 -m pytest -q"},
            "issue_number": 263,
            "base_ref": "main",
        }
    )
    second = valid_event(
        attributes={
            "base_ref": "main",
            "issue_number": 263,
            "validation": {"command": "python3 -m pytest -q", "passed": True},
        }
    )

    payload = event_ledger_event_to_json(first)

    assert payload == event_ledger_event_to_json(second)
    assert payload == first.to_json()
    assert payload == json.dumps(json.loads(payload), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    assert json.loads(payload)["attributes"]["validation"]["passed"] is True


def test_attributes_are_normalized_without_mutating_input() -> None:
    attributes = {
        "note": " validated\n without storage ",
        "items": ("alpha", {"count": 2}),
        "pairs": [("label", 1)],
    }
    event = valid_event(attributes=attributes)
    attributes["note"] = "changed after construction"

    assert event_ledger_event_to_dict(event)["attributes"] == {
        "items": ["alpha", {"count": 2}],
        "note": "validated without storage",
        "pairs": [["label", 1]],
    }


@pytest.mark.parametrize("status", sorted(ALLOWED_EVENT_STATUSES))
def test_supported_statuses_pass(status: str) -> None:
    assert valid_event(status=status).status == status


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("workflow_id", "Runner"),
        ("event_id", "issue 263"),
        ("event_type", "task/validated"),
        ("status", "merged"),
        ("actor_reference", "oleksii"),
        ("timestamp", "2026-05-22T08:30:00+00:00"),
        ("summary", ""),
        ("attributes", []),
    ),
)
def test_malformed_events_are_blocked(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        valid_event(**{field: value})


def test_bounded_fields_are_enforced() -> None:
    with pytest.raises(ValueError, match="summary must be at most"):
        valid_event(summary="x" * (MAX_SUMMARY_CHARS + 1))
    with pytest.raises(ValueError, match="strings must be at most"):
        valid_event(attributes={"detail": "x" * (MAX_ATTRIBUTE_STRING_CHARS + 1)})
    with pytest.raises(ValueError, match="at most"):
        valid_event(attributes={f"key_{index}": index for index in range(MAX_ATTRIBUTE_KEYS + 1)})


@pytest.mark.parametrize(
    "attributes",
    (
        {"token": "ghp_example"},
        {"nested": {"secret": "value"}},
        {"items": [{"password": "value"}]},
        {"source_text": "raw private source"},
    ),
)
def test_unsafe_attribute_keys_are_blocked(attributes: object) -> None:
    with pytest.raises(ValueError, match="unsafe field"):
        valid_event(attributes=attributes)


@pytest.mark.parametrize(
    "attributes",
    (
        {"bad key": "value"},
        {"nan": float("nan")},
        {"object": object()},
        {"too_deep": {"a": {"b": {"c": {"d": "value"}}}}},
    ),
)
def test_non_json_safe_attributes_are_blocked(attributes: object) -> None:
    with pytest.raises(ValueError):
        valid_event(attributes=attributes)


def test_validation_helper_is_public_and_specific() -> None:
    assert validate_identifier("task.validated", "event_type") == "task.validated"
    with pytest.raises(ValueError, match="custom_field"):
        validate_identifier("Task Validated", "custom_field")


def test_helpers_do_not_use_network_subprocess_or_files() -> None:
    with mock.patch.object(subprocess, "run") as run, mock.patch.object(
        subprocess, "Popen"
    ) as popen, mock.patch.object(urllib.request, "urlopen") as urlopen, mock.patch(
        "builtins.open", mock.mock_open()
    ) as opened:
        payload = valid_event().to_json()

    assert json.loads(payload)["schema"] == EVENT_LEDGER_SCHEMA
    run.assert_not_called()
    popen.assert_not_called()
    urlopen.assert_not_called()
    opened.assert_not_called()


def test_import_has_no_file_side_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.iterdir())

    import core.event_ledger as event_ledger

    importlib.reload(event_ledger)

    assert set(tmp_path.iterdir()) == before
