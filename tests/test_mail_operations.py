import json

import pytest

from core.mail_operations import (
    MailOperationError,
    build_scheduler_deadline_checkpoint,
    normalize_correspondence,
    process_important_mail,
    public_mail_operation_receipt,
    render_draft_in_source_language,
    replay_deadline_checkpoint,
)
from core.scheduler_models import ScheduleSpec


def _mail(**updates):
    packet = {
        "provider": "synthetic",
        "provider_message_ref": "provider-message-1",
        "thread_ref": "thread-42",
        "sender_ref": "sender-opaque",
        "received_at": 1786400000,
        "subject_hint": "Important deadline for correspondence",
        "body_preview": "Please review. Deadline 2026-09-01. No private mailbox body is used.",
        "importance_hint": "high",
        "deadline_hint": "2026-09-01",
    }
    packet.update(updates)
    return packet


def test_important_mail_normalizes_stable_case_and_correspondence_refs() -> None:
    first = normalize_correspondence(_mail())
    second = normalize_correspondence(_mail(provider="imap"))

    assert first.case_ref == second.case_ref
    assert first.correspondence_ref != second.correspondence_ref
    assert first.important is True
    assert first.deadline_at == 1788253200


def test_deadline_replay_creates_exactly_one_scheduler_checkpoint() -> None:
    normalized = normalize_correspondence(_mail())
    checkpoint = build_scheduler_deadline_checkpoint(normalized, _mail(), now=1786400010)
    replayed = replay_deadline_checkpoint((checkpoint,), checkpoint)

    assert len(replayed) == 1
    assert replayed[0].schedule_id == checkpoint.schedule_id
    assert replayed[0].route_id == "mail.operator_checkpoint"
    assert replayed[0].approval_policy == "require_operator_each_occurrence"
    assert replayed[0].payload["task_packet"]["case_ref"] == normalized.case_ref


def test_operator_presentation_is_ukrainian_actionable_and_public_safe() -> None:
    receipt = process_important_mail(_mail(), now=1786400010)
    packet = receipt["operator_packet"]

    assert receipt["status"] == "NEEDS_OPERATOR"
    assert packet["language"] == "uk"
    assert "Важлива кореспонденція" in packet["summary_uk"]
    assert packet["telegram_reply_contract"]["actionable"] is True
    assert {action["id"] for action in packet["telegram_reply_contract"]["allowed_actions"]} >= {
        "approve_reply",
        "revise_reply",
        "defer",
        "confirm_deadline",
    }
    public_json = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    assert "provider-message-1" not in public_json
    assert "sender-opaque" not in public_json
    assert "No private mailbox body" not in public_json


def test_semantic_draft_hash_survives_source_language_render() -> None:
    receipt = process_important_mail(_mail(), now=1786400010)
    draft = receipt["draft_revision"]
    rendered = render_draft_in_source_language(draft, source_language="en")

    assert rendered["approved_semantic_hash"] == draft["approved_semantic_hash"]
    assert rendered["semantic_hash_verified"] is True

    with pytest.raises(MailOperationError) as error:
        render_draft_in_source_language(
            draft,
            source_language="en",
            approved_semantic_hash="0" * 64,
        )
    assert error.value.reason_code == "SEMANTIC_DRAFT_HASH_MISMATCH"


def test_public_receipts_redact_private_payload_values() -> None:
    receipt = public_mail_operation_receipt(
        {
            "schema": "synthetic",
            "status": "DONE",
            "from": "person@example.invalid",
            "body": "private mail body",
            "nested": {"attachments": ["invoice.pdf"], "safe": "kept"},
        }
    )

    assert receipt["from"] == "[REDACTED]"
    assert receipt["body"] == "[REDACTED]"
    assert receipt["nested"]["attachments"] == "[REDACTED]"
    assert receipt["nested"]["safe"] == "kept"


def test_scheduler_checkpoint_is_valid_schedule_spec_mapping() -> None:
    normalized = normalize_correspondence(_mail())
    checkpoint = build_scheduler_deadline_checkpoint(normalized, _mail(), now=1786400010)

    round_trip = ScheduleSpec.from_mapping(checkpoint.to_mapping())
    assert round_trip.schedule_id == checkpoint.schedule_id
