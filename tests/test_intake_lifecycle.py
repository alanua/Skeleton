from __future__ import annotations

import pytest

from core.intake_lifecycle import (
    IntakeLifecycleError,
    IntakeLifecycleStore,
    reconcile_lifecycle,
    stable_intake_id,
)


def test_lifecycle_identity_is_stable_before_processing(tmp_path) -> None:
    store = IntakeLifecycleStore(tmp_path / "state.sqlite3")
    first = store.record(
        item_kind="document",
        source_ref="document:scanner-a",
        source_hash="a" * 64,
        state="RECEIVED",
        blocker_reason="AWAITING_PRESERVATION",
        next_action="preserve_original_artifact",
        provenance_refs=("document:scanner-a",),
        artifact_refs=(
            {
                "artifact_ref": "artifact:scanner-a",
                "artifact_sha256": "a" * 64,
                "kind": "original",
            },
        ),
        branch_ref="chat-a",
        now=100,
    )
    second = store.record(
        item_kind="document",
        source_ref="document:scanner-a",
        source_hash="a" * 64,
        state="CLASSIFIED",
        blocker_reason="AWAITING_MEMORY_ARCHIVE",
        next_action="archive_document_record",
        provenance_refs=("family_document:doc-a",),
        record_ref="family_document:doc-a",
        branch_ref="chat-b",
        now=110,
    )

    assert first.intake_id == second.intake_id
    assert first.intake_id == stable_intake_id(
        item_kind="document",
        source_ref="document:scanner-a",
        source_hash="a" * 64,
    )
    pending = store.pending_work()
    assert pending[0]["state"] == "CLASSIFIED"
    assert pending[0]["next_action"] == "archive_document_record"
    assert pending[0]["artifact_count"] == 1


def test_nonterminal_items_require_actionable_blocker_metadata(tmp_path) -> None:
    store = IntakeLifecycleStore(tmp_path / "state.sqlite3")

    with pytest.raises(IntakeLifecycleError):
        store.record(
            item_kind="mail",
            source_ref="acct:primary",
            source_hash="b" * 64,
            state="BLOCKED",
            blocker_reason="NONE",
            next_action="none",
        )


def test_synthetic_orphan_recovery_is_public_safe_and_queryable_across_branches(tmp_path) -> None:
    store = IntakeLifecycleStore(tmp_path / "state.sqlite3")

    receipt = reconcile_lifecycle(
        store,
        observed_artifacts=(
            {
                "artifact_ref": "artifact:mfp-orphan",
                "artifact_sha256": "c" * 64,
                "kind": "original",
            },
        ),
        known_source_hashes=(),
        now=200,
    )

    assert receipt["orphaned_artifacts"] == 1
    assert receipt["private_payloads_included"] is False
    pending = store.pending_work()
    assert pending[0]["state"] == "BLOCKED"
    assert pending[0]["blocker_reason"] == "ORPHANED_ARTIFACT"
    assert pending[0]["next_action"] == "operator_reconcile_or_import_original_artifact"

    restarted = IntakeLifecycleStore(tmp_path / "state.sqlite3")
    assert restarted.pending_work()[0]["intake_id"] == pending[0]["intake_id"]


def test_stalled_nonterminal_work_is_deferred_for_resume(tmp_path) -> None:
    store = IntakeLifecycleStore(tmp_path / "state.sqlite3")
    store.record(
        item_kind="document",
        source_ref="document:stalled",
        source_hash="d" * 64,
        state="PROCESSING",
        blocker_reason="WORKER_EXITED",
        next_action="resume_document_intake",
        provenance_refs=("document:stalled",),
        now=10,
    )

    receipt = reconcile_lifecycle(store, stalled_before=20, now=30)

    assert receipt["stalled_items"] == 1
    assert store.pending_work()[0]["state"] == "DEFERRED"
    assert store.pending_work()[0]["next_action"] == "resume_nonterminal_intake_work"
