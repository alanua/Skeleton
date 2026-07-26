from __future__ import annotations

from core.family_document_runtime import FamilyDocumentRuntime, private_repair_handoff, read_json


def test_runtime_persists_sequences_and_private_repair_handoff(tmp_path):
    runtime = FamilyDocumentRuntime.open(tmp_path / "runtime")
    assert runtime.next_sequence() == 1
    assert FamilyDocumentRuntime.open(runtime.root).next_sequence() == 2

    handoff = private_repair_handoff(
        runtime=runtime,
        repair_id="repair-one",
        component_record_ids=["c1"],
        supersedes_document_ids=["d1"],
        merged_document_id="merged",
    )

    stored = read_json(runtime.root / "handoff" / "repair-one.json")
    assert stored == handoff
    assert stored["action"] == "create_merged_logical_document_without_deletion"
