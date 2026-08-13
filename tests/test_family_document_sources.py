from __future__ import annotations

import os
import time

from core.family_document_sources import ApprovedLocalSourceInventory, StableFileGate, approved_source_inventory_receipt


def test_inventory_uses_approved_roots_and_extensions(tmp_path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.txt").write_text("ok", encoding="utf-8")
    (inbox / "skip.exe").write_text("no", encoding="utf-8")
    items = ApprovedLocalSourceInventory((inbox,)).iter_candidates()
    assert [item.path.name for item in items] == ["a.txt"]
    receipt = approved_source_inventory_receipt(items)
    assert receipt["privacy"] == "aggregate_only"


def test_stable_file_gate_rejects_too_new_then_accepts(tmp_path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("ok", encoding="utf-8")
    stable, meta = StableFileGate(min_age_seconds=60).check(source)
    assert stable is False
    assert meta["reason"] == "FILE_TOO_NEW"
    old = time.time() - 120
    os.utime(source, (old, old))
    stable, meta = StableFileGate(min_age_seconds=1).check(source)
    assert stable is True
    assert meta["reason"] == "STABLE"
