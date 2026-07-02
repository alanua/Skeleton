from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from core.mempalace_adapter import LocalMemPalaceIndex
from core.graphify_adapter import LocalGraphifyIndex
from core.private_memory_bundle import PRIVATE_MEMORY_IMPORT_RECEIPT_NAMESPACE
from core.private_memory_history import bytes_hash, content_hash
from core.private_memory_stack import PrivateMemoryStack, PrivateMemoryStackError


def test_valid_multi_record_bundle_imports_atomically_and_rebuilds_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = PrivateMemoryStack(tmp_path / "pm")
    stack.init(import_manifest=False)
    inbox = _inbox(tmp_path)
    bundle_path = _write_bundle(inbox, _bundle("bundle1"))
    counts = {"mempalace": 0, "graphify": 0}
    original_mempalace = LocalMemPalaceIndex.rebuild_from_facts
    original_graphify = LocalGraphifyIndex.rebuild_from_facts

    def count_mempalace(*args: object, **kwargs: object) -> dict[str, object]:
        counts["mempalace"] += 1
        return original_mempalace(*args, **kwargs)

    def count_graphify(*args: object, **kwargs: object) -> dict[str, object]:
        counts["graphify"] += 1
        return original_graphify(*args, **kwargs)

    monkeypatch.setattr(LocalMemPalaceIndex, "rebuild_from_facts", count_mempalace)
    monkeypatch.setattr(LocalGraphifyIndex, "rebuild_from_facts", count_graphify)

    receipt = stack.import_bundle(
        bundle_path.name,
        expected_sha256=bytes_hash(bundle_path.read_bytes()),
        env={"SKELETON_PRIVATE_MEMORY_INBOX": str(inbox)},
    )

    assert receipt["status"] == "DONE"
    assert receipt["record_count"] == 2
    assert counts == {"mempalace": 1, "graphify": 1}
    assert stack.get(namespace="skeleton.notes", fact_id="alpha")["value_hash"] == content_hash(
        {"summary": "alpha ventilation", "tags": ["ops"]}
    )
    assert stack.get(namespace=PRIVATE_MEMORY_IMPORT_RECEIPT_NAMESPACE, fact_id="bundle1")["value"][
        "imported_canonical_refs"
    ][0]["value_hash"]
    assert not bundle_path.exists()
    assert (inbox / "processed" / f"{receipt['receipt_id']}.json").is_file()
    assert "alpha ventilation" not in json.dumps(receipt)


def test_mid_batch_failure_restores_facts_history_revision_and_indexes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = PrivateMemoryStack(tmp_path / "pm")
    stack.init(import_manifest=False)
    before = stack.status()
    inbox = _inbox(tmp_path)
    bundle_path = _write_bundle(inbox, _bundle("bundle2"))

    def partial_then_fail(*args: object, **kwargs: object) -> list[dict[str, object]]:
        stack.store.put_fact(
            namespace="skeleton.notes",
            fact_id="partial",
            value={"summary": "should rollback"},
            actor_ref="operator",
            reason_code="test",
            approval_ref="local",
            transaction_ref="partial",
        )
        raise RuntimeError("synthetic mid batch failure")

    monkeypatch.setattr(stack.store, "bulk_put_facts", partial_then_fail)

    with pytest.raises(PrivateMemoryStackError):
        stack.import_bundle(
            bundle_path.name,
            expected_sha256=bytes_hash(bundle_path.read_bytes()),
            env={"SKELETON_PRIVATE_MEMORY_INBOX": str(inbox)},
        )

    after = stack.status()
    assert after["canonical_sqlite"]["canonical_revision"] == before["canonical_sqlite"]["canonical_revision"]
    assert after["canonical_sqlite"]["event_count"] == before["canonical_sqlite"]["event_count"]
    assert after["mempalace"]["indexed_canonical_revision"] == before["mempalace"]["indexed_canonical_revision"]
    assert bundle_path.exists()
    with pytest.raises(PrivateMemoryStackError):
        stack.get(namespace="skeleton.notes", fact_id="partial")


def test_index_rebuild_failure_restores_pre_operation_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stack = PrivateMemoryStack(tmp_path / "pm")
    stack.init(import_manifest=False)
    before = stack.status()["canonical_sqlite"]["canonical_revision"]
    inbox = _inbox(tmp_path)
    bundle_path = _write_bundle(inbox, _bundle("bundle3"))

    def fail_rebuild(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("synthetic rebuild failure")

    monkeypatch.setattr(LocalGraphifyIndex, "rebuild_from_facts", fail_rebuild)

    with pytest.raises(PrivateMemoryStackError):
        stack.import_bundle(
            bundle_path.name,
            expected_sha256=bytes_hash(bundle_path.read_bytes()),
            env={"SKELETON_PRIVATE_MEMORY_INBOX": str(inbox)},
        )

    assert stack.status()["canonical_sqlite"]["canonical_revision"] == before
    assert bundle_path.exists()


def test_duplicate_same_hash_is_idempotent_and_different_hash_blocks(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path / "pm")
    stack.init(import_manifest=False)
    inbox = _inbox(tmp_path)
    first = _write_bundle(inbox, _bundle("bundle4"), name="first.json")
    first_receipt = stack.import_bundle(
        first.name,
        expected_sha256=bytes_hash(first.read_bytes()),
        env={"SKELETON_PRIVATE_MEMORY_INBOX": str(inbox)},
    )
    revision = first_receipt["canonical_revision"]
    duplicate = _write_bundle(inbox, _bundle("bundle4"), name="duplicate.json")

    second = stack.import_bundle(
        duplicate.name,
        expected_sha256=bytes_hash(duplicate.read_bytes()),
        env={"SKELETON_PRIVATE_MEMORY_INBOX": str(inbox)},
    )

    assert second["idempotency_classification"] == "DUPLICATE_IDENTICAL"
    assert second["canonical_revision"] == revision

    changed = _bundle("bundle4")
    changed["records"][0]["value"] = {"summary": "changed"}
    blocked = _write_bundle(inbox, changed, name="changed.json")
    with pytest.raises(PrivateMemoryStackError):
        stack.import_bundle(
            blocked.name,
            expected_sha256=bytes_hash(blocked.read_bytes()),
            env={"SKELETON_PRIVATE_MEMORY_INBOX": str(inbox)},
        )
    assert blocked.exists()


@pytest.mark.parametrize(
    ("name", "make_file", "expected_sha"),
    [
        ("../escape.json", lambda inbox: inbox / "missing.json", "0" * 64),
        ("/absolute.json", lambda inbox: inbox / "missing.json", "0" * 64),
        ("link.json", lambda inbox: _symlink_bundle(inbox), "0" * 64),
        ("large.json", lambda inbox: _oversized_bundle(inbox), None),
        ("open.json", lambda inbox: _permissive_bundle(inbox), None),
        ("sha.json", lambda inbox: _raw_bundle(inbox, b"{not json"), "f" * 64),
    ],
)
def test_inbox_boundary_blocks_before_parsing_or_mutation(
    tmp_path: Path, name: str, make_file: object, expected_sha: str | None
) -> None:
    stack = PrivateMemoryStack(tmp_path / "pm")
    stack.init(import_manifest=False)
    inbox = _inbox(tmp_path)
    path = make_file(inbox)  # type: ignore[misc]
    sha = expected_sha or bytes_hash(path.read_bytes())
    before = stack.status()["canonical_sqlite"]["canonical_revision"]

    with pytest.raises(Exception):
        stack.import_bundle(name, expected_sha256=sha, env={"SKELETON_PRIVATE_MEMORY_INBOX": str(inbox)})

    assert stack.status()["canonical_sqlite"]["canonical_revision"] == before
    if path.exists() and not path.is_symlink():
        assert path.exists()


def _bundle(bundle_id: str) -> dict[str, object]:
    return {
        "schema": "skeleton.private_memory_import_bundle.v1",
        "bundle_id": bundle_id,
        "privacy_class": "LOCAL_PRIVATE",
        "operator_approved": True,
        "record_count": 2,
        "records": [
            {
                "namespace": "skeleton.notes",
                "fact_id": "alpha",
                "actor": "operator",
                "reason": "approved-import",
                "approval": "local-approval",
                "value": {"summary": "alpha ventilation", "tags": ["ops"]},
            },
            {
                "namespace": "skeleton.notes",
                "fact_id": "beta",
                "actor": "operator",
                "reason": "approved-import",
                "approval": "local-approval",
                "value": {"summary": "beta relay", "relationships": [{"kind": "supports", "target": "runbook"}]},
            },
        ],
    }


def _inbox(tmp_path: Path) -> Path:
    inbox = tmp_path / "inbox"
    inbox.mkdir(mode=0o700)
    inbox.chmod(0o700)
    return inbox


def _write_bundle(inbox: Path, bundle: dict[str, object], *, name: str = "bundle.json") -> Path:
    return _raw_bundle(inbox, json.dumps(bundle, sort_keys=True).encode("utf-8"), name=name)


def _raw_bundle(inbox: Path, raw: bytes, *, name: str = "sha.json") -> Path:
    path = inbox / name
    path.write_bytes(raw)
    path.chmod(0o600)
    return path


def _symlink_bundle(inbox: Path) -> Path:
    target = _raw_bundle(inbox, b"{not json", name="target.json")
    link = inbox / "link.json"
    os.symlink(target, link)
    return link


def _oversized_bundle(inbox: Path) -> Path:
    path = inbox / "large.json"
    path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    path.chmod(0o600)
    return path


def _permissive_bundle(inbox: Path) -> Path:
    path = _raw_bundle(inbox, b"{not json", name="open.json")
    path.chmod(0o644)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    return path
