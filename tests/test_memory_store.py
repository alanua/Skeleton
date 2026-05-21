from __future__ import annotations

import importlib
import json
import subprocess
import urllib.request
from pathlib import Path
from unittest.mock import Mock

import pytest

from core.memory_manager import MemoryRecord, route_memory_record
from core.memory_store import (
    CONTENT_PREVIEW_LIMIT,
    MEMORY_LEDGER_SCHEMA,
    PRIVATE_CONTENT_PREVIEW,
    SESSION_STATE_SNAPSHOT_SCHEMA,
    append_memory_ledger_entry,
    build_memory_ledger_entry,
    memory_ledger_entry_to_dict,
    redact_private_content,
    write_session_state_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "memory_store.schema.json"


def record(**overrides: object) -> MemoryRecord:
    values = {
        "id": "mem-store-001",
        "project_id": "skeleton",
        "memory_type": "project_state",
        "source": "current_user_message",
        "trust_level": "runtime_direct",
        "content": "Public stage 1 memory store audit preview.",
        "status": "active",
        "created_at": "2026-05-21T00:00:00Z",
        "public_safe": True,
        "critique_present": False,
        "operator_approved": False,
        "changes_canon_or_instruction": False,
    }
    values.update(overrides)
    return MemoryRecord(**values)


def ledger_entry(**overrides: object):
    memory_record = record(**overrides)
    return build_memory_ledger_entry(memory_record, route_memory_record(memory_record))


def snapshot() -> dict[str, object]:
    return {
        "schema": SESSION_STATE_SNAPSHOT_SCHEMA,
        "public_safe": True,
        "session_id": "session-001",
        "project_id": "skeleton",
        "state": {
            "phase": "audit",
            "routes": ["project_state", "weak_cache"],
        },
    }


def test_public_safe_ledger_entry_stores_bounded_preview() -> None:
    public_record = record(content=("route preview " * 40).strip())
    entry = build_memory_ledger_entry(public_record, route_memory_record(public_record))
    stored = memory_ledger_entry_to_dict(entry)

    assert stored["schema"] == MEMORY_LEDGER_SCHEMA
    assert stored["record_id"] == "mem-store-001"
    assert stored["route_status"] == "accepted"
    assert stored["target_route"] == "project_state"
    assert stored["content_preview"] != public_record.content
    assert len(stored["content_preview"]) == CONTENT_PREVIEW_LIMIT
    assert stored["content_preview"].endswith("...")
    assert "content" not in stored


def test_non_public_record_content_is_redacted() -> None:
    private_record = record(public_safe=False, content="operator secret context must not persist")
    entry = build_memory_ledger_entry(private_record, route_memory_record(private_record))

    assert redact_private_content(private_record) == PRIVATE_CONTENT_PREVIEW
    assert memory_ledger_entry_to_dict(entry)["content_preview"] == PRIVATE_CONTENT_PREVIEW
    assert private_record.content not in json.dumps(memory_ledger_entry_to_dict(entry))


def test_jsonl_append_writes_only_explicit_tmp_path(tmp_path: Path) -> None:
    ledger_path = tmp_path / "memory-ledger.jsonl"

    returned = append_memory_ledger_entry(ledger_path, ledger_entry())
    line = ledger_path.read_text(encoding="utf-8").splitlines()

    assert returned == ledger_path
    assert set(tmp_path.iterdir()) == {ledger_path}
    assert len(line) == 1
    assert json.loads(line[0])["record_id"] == "mem-store-001"
    assert line[0] == json.dumps(json.loads(line[0]), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def test_multiple_jsonl_appends_preserve_order(tmp_path: Path) -> None:
    ledger_path = tmp_path / "memory-ledger.jsonl"

    append_memory_ledger_entry(ledger_path, ledger_entry(id="mem-store-001"))
    append_memory_ledger_entry(ledger_path, ledger_entry(id="mem-store-002"))

    rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    assert [row["record_id"] for row in rows] == ["mem-store-001", "mem-store-002"]


def test_snapshot_json_is_deterministic(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "session-state.json"
    unsorted_snapshot = snapshot()
    unsorted_snapshot["state"] = {"routes": ["project_state", "weak_cache"], "phase": "audit"}

    write_session_state_snapshot(snapshot_path, unsorted_snapshot)

    assert snapshot_path.read_text(encoding="utf-8") == (
        '{\n'
        '  "project_id": "skeleton",\n'
        '  "public_safe": true,\n'
        '  "schema": "skeleton.memory_store.session_state_snapshot.v1",\n'
        '  "session_id": "session-001",\n'
        '  "state": {\n'
        '    "phase": "audit",\n'
        '    "routes": [\n'
        '      "project_state",\n'
        '      "weak_cache"\n'
        '    ]\n'
        '  }\n'
        '}\n'
    )


def test_snapshot_rejects_unsafe_content(tmp_path: Path) -> None:
    unsafe_snapshot = snapshot()
    unsafe_snapshot["state"] = {"content": "private memory body"}

    with pytest.raises(ValueError, match="unsafe content field: content"):
        write_session_state_snapshot(tmp_path / "unsafe.json", unsafe_snapshot)
    with pytest.raises(ValueError, match="public_safe must be true"):
        write_session_state_snapshot(tmp_path / "private.json", {**snapshot(), "public_safe": False})

    assert not (tmp_path / "unsafe.json").exists()
    assert not (tmp_path / "private.json").exists()


def test_store_functions_do_not_use_network_or_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    urlopen_mock = Mock(side_effect=AssertionError("urlopen must not be called"))
    run_mock = Mock(side_effect=AssertionError("subprocess.run must not be called"))
    popen_mock = Mock(side_effect=AssertionError("subprocess.Popen must not be called"))
    monkeypatch.setattr(urllib.request, "urlopen", urlopen_mock)
    monkeypatch.setattr(subprocess, "run", run_mock)
    monkeypatch.setattr(subprocess, "Popen", popen_mock)

    append_memory_ledger_entry(tmp_path / "memory-ledger.jsonl", ledger_entry())
    write_session_state_snapshot(tmp_path / "session-state.json", snapshot())

    urlopen_mock.assert_not_called()
    run_mock.assert_not_called()
    popen_mock.assert_not_called()


def test_import_has_no_file_side_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.iterdir())

    import core.memory_store as memory_store

    importlib.reload(memory_store)

    assert set(tmp_path.iterdir()) == before


def test_schema_exists_and_documents_expected_fields() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    ledger_schema = schema["$defs"]["ledger_entry"]
    snapshot_schema = schema["$defs"]["session_state_snapshot"]

    assert schema["$id"] == "skeleton.memory_store.schema.json"
    assert ledger_schema["properties"]["schema"]["const"] == MEMORY_LEDGER_SCHEMA
    assert ledger_schema["properties"]["content_preview"]["maxLength"] == CONTENT_PREVIEW_LIMIT
    assert ledger_schema["required"] == list(memory_ledger_entry_to_dict(ledger_entry()).keys())
    assert snapshot_schema["properties"]["public_safe"]["const"] is True
    assert set(snapshot_schema["required"]) == {
        "schema",
        "public_safe",
        "session_id",
        "project_id",
        "state",
    }
