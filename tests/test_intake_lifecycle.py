from __future__ import annotations

import pytest

from core.intake_lifecycle import (
    IntakeLifecycleError,
    IntakeLifecycleStore,
    reconcile_lifecycle,
    shared_pending_lifecycle_work,
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


def test_blocked_and_deferred_items_resume_with_explicit_transition_policy(tmp_path) -> None:
    store = IntakeLifecycleStore(tmp_path / "state.sqlite3")
    blocked = store.record(
        item_kind="mail",
        source_ref="acct:primary",
        source_hash="e" * 64,
        state="BLOCKED",
        blocker_reason="OAUTH_REAUTHORIZATION_REQUIRED",
        next_action="operator_reauthorize_then_resume",
        provenance_refs=("mail-account:e",),
        now=10,
    )

    resumed = store.record(
        item_kind="mail",
        source_ref="acct:primary",
        source_hash="e" * 64,
        state="PROCESSING",
        blocker_reason="MAIL_PROCESSING_IN_PROGRESS",
        next_action="classify_mail_for_operator_action",
        provenance_refs=("mail-account:e",),
        now=20,
    )

    assert resumed.intake_id == blocked.intake_id
    assert resumed.state == "PROCESSING"
    assert resumed.blocker_reason == "MAIL_PROCESSING_IN_PROGRESS"
    assert resumed.next_action == "classify_mail_for_operator_action"

    deferred = store.update_existing(
        resumed.intake_id,
        state="DEFERRED",
        blocker_reason="BACKOFF_ACTIVE",
        next_action="resume_after_backoff",
        now=30,
    )
    assert deferred.state == "DEFERRED"

    resumed_again = store.update_existing(
        resumed.intake_id,
        state="PROCESSING",
        blocker_reason="MAIL_PROCESSING_IN_PROGRESS",
        next_action="classify_mail_for_operator_action",
        now=40,
    )
    assert resumed_again.state == "PROCESSING"


def test_shared_pending_lifecycle_work_is_public_safe_and_read_only(tmp_path) -> None:
    document_store = IntakeLifecycleStore(tmp_path / "document.sqlite3")
    mail_store = IntakeLifecycleStore(tmp_path / "mail.sqlite3")
    document_store.record(
        item_kind="document",
        source_ref="document:scan-a",
        source_hash="1" * 64,
        state="DEFERRED",
        blocker_reason="BACKOFF_ACTIVE",
        next_action="retry_document_intake_after_backoff",
        provenance_refs=("document:scan-a",),
        now=30,
    )
    mail_store.record(
        item_kind="mail",
        source_ref="acct:primary",
        source_hash="2" * 64,
        state="BLOCKED",
        blocker_reason="OPERATOR_ACTION_REQUIRED",
        next_action="operator_review_action_required_mail",
        provenance_refs=("mail:message-a",),
        now=20,
    )

    receipt = shared_pending_lifecycle_work({"document": document_store, "mail": mail_store}, limit=10)

    assert receipt["schema"] == "skeleton.shared_pending_lifecycle_work.v1"
    assert receipt["public_safe"] is True
    assert receipt["private_payloads_included"] is False
    assert receipt["external_side_effects_executed"] is False
    assert receipt["pending_count"] == 2
    assert receipt["state_counts"]["BLOCKED"] == 1
    assert receipt["state_counts"]["DEFERRED"] == 1
    assert receipt["item_kind_counts"] == {"document": 1, "mail": 1}
    assert [item["store_ref"] for item in receipt["items"]] == ["mail", "document"]
    assert document_store.pending_work()[0]["state"] == "DEFERRED"
    assert mail_store.pending_work()[0]["state"] == "BLOCKED"
