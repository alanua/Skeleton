from __future__ import annotations

import json
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from core.canonical_memory import FAST_AUTONOMOUS_EXECUTION_KEY
from core.mempalace_adapter import LocalMemPalaceIndex
from core.private_memory_stack import PrivateMemoryStack


ROOT = Path(__file__).resolve().parents[1]


def test_init_creates_private_root_sqlite_wal_indexes_and_imports_manifest(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path)

    status = stack.init()
    exact = stack.get(
        namespace="skeleton.operator_preferences",
        fact_id=FAST_AUTONOMOUS_EXECUTION_KEY,
    )

    assert status["state"] == "READY"
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "canonical.sqlite").stat().st_mode) == 0o600
    assert exact["authoritative"] is True
    assert exact["value"]["key"] == FAST_AUTONOMOUS_EXECUTION_KEY
    assert (tmp_path / "mempalace.index.json").is_file()
    assert (tmp_path / "graphify.index.json").is_file()


def test_init_preserves_non_empty_valid_database_and_manifest_is_idempotent(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init()
    before = stack.status()["canonical_sqlite"]["canonical_revision"]

    second = PrivateMemoryStack(tmp_path).init()

    assert second["state"] == "READY"
    assert second["canonical_sqlite"]["canonical_revision"] == before


def test_put_rebuilds_indexes_and_exact_get_reads_sqlite(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init()

    mutation = stack.put(
        namespace="skeleton.notes",
        fact_id="note1",
        value={
            "summary": "alpha beta ventilation",
            "tags": ["ops"],
            "relationships": [{"kind": "supports", "target": "runbook"}],
        },
    )
    exact = stack.get(namespace="skeleton.notes", fact_id="note1")
    semantic = stack.search(query="ventilation", limit=3)
    relations = stack.relations(query="runbook", limit=3)

    assert mutation["status"] == "DONE"
    assert exact["value"]["summary"] == "alpha beta ventilation"
    assert exact["authority_classification"] == "canonical_sqlite"
    assert semantic["authoritative"] is False
    assert semantic["results"][0]["canonical_ref"] == "skeleton.notes:note1"
    assert semantic["results"][0]["source_attribution"][0]["canonical_revision"] == exact["canonical_revision"]
    assert relations["authoritative"] is False
    assert relations["results"][0]["canonical_ref"] == "skeleton.notes:note1"


def test_status_schema_has_no_raw_private_content_or_paths(tmp_path: Path) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    stack = PrivateMemoryStack(tmp_path)
    stack.init()
    stack.put(namespace="skeleton.notes", fact_id="private1", value={"summary": "private phrase xyz"})

    status = stack.status()
    schema = json.loads((ROOT / "schemas" / "private_memory_stack_status.schema.json").read_text(encoding="utf-8"))
    serialized = json.dumps(status, sort_keys=True)

    jsonschema.Draft202012Validator(schema).validate(status)
    assert str(tmp_path) not in serialized
    assert "private phrase xyz" not in serialized


def test_stale_detection_after_canonical_change_without_rebuild(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init()
    stack.store.put_fact(
        namespace="skeleton.notes",
        fact_id="stale1",
        value={"summary": "manual canonical update"},
        actor_ref="operator",
        reason_code="manual-test",
        approval_ref="local-operator",
        transaction_ref="manual-test",
    )

    status = stack.status()

    assert status["state"] == "STALE"
    assert status["mempalace"]["state"] == "STALE"
    assert status["graphify"]["state"] == "STALE"


def test_backup_is_local_aggregate_report(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init()

    backup = stack.backup(snapshot_id="snapshot-test")

    assert backup["status"] == "DONE"
    assert backup["snapshot_id"] == "snapshot-test"
    assert "aggregate_counts" in backup
    assert (tmp_path / "backups" / "snapshot-test.sqlite").is_file()


def test_rollback_restores_canonical_database_when_index_rebuild_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init()
    before = stack.status()["canonical_sqlite"]["canonical_revision"]

    def fail_rebuild(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("synthetic rebuild failure")

    monkeypatch.setattr(LocalMemPalaceIndex, "rebuild_from_facts", fail_rebuild)

    with pytest.raises(Exception):
        stack.put(namespace="skeleton.notes", fact_id="willrollback", value={"summary": "rollback"})

    with sqlite3.connect(tmp_path / "canonical.sqlite") as connection:
        revision = connection.execute(
            "SELECT current_revision FROM private_memory_canonical_revision WHERE id = 1"
        ).fetchone()[0]
        row = connection.execute(
            "SELECT COUNT(*) FROM private_memory_facts WHERE namespace = 'skeleton.notes'"
        ).fetchone()[0]
    assert revision == before
    assert row == 0


def test_cli_help_and_installer_syntax() -> None:
    help_result = subprocess.run(
        [sys.executable, "scripts/skeleton_private_memory.py", "--help"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "init" in help_result.stdout

    subprocess.run(["bash", "-n", "scripts/install_skeleton_private_memory.sh"], cwd=ROOT, check=True)
