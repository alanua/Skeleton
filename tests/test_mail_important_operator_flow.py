from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from core.mail_important_operator_flow import (
    CALLBACK_SCHEMA,
    FLOW_SCHEMA,
    process_important_mail_event,
    render_source_language_reply_prompt,
)
from core.scheduler_store import SchedulerStore


PRIVATE_MARKER = "SYNTH_PRIVATE_MARKER_CASE_2385"


def _event(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "synthetic_fixture": True,
        "provider": "synthetic_mail_fixture",
        "event_id": "synthetic-important-mail-001",
        "received_at": "2026-08-09T10:00:00Z",
        "from_name": "Synthetic Client",
        "from_address": "synthetic.client@example.invalid",
        "subject": "Urgent contract review",
        "body_text": (
            "Hello, please review the attached contract summary and confirm by "
            f"2026-08-12. Private marker {PRIVATE_MARKER}."
        ),
        "importance": "important",
    }
    value.update(overrides)
    return value


def _flow(tmp_path, event: dict[str, object] | None = None):
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    result = process_important_mail_event(
        event or _event(),
        scheduler_store=store,
        now=int(datetime(2026, 8, 9, 11, 0, tzinfo=timezone.utc).timestamp()),
    )
    return result, store


def test_important_mail_normalizes_case_correspondence_and_ukrainian_operator_text(
    tmp_path,
) -> None:
    result, _store = _flow(tmp_path)

    assert result["schema"] == FLOW_SCHEMA
    assert result["case"]["case_ref"].startswith("case_mail_")
    assert result["correspondence"]["correspondence_ref"].startswith("corr_")
    assert result["correspondence"]["case_ref"] == result["case"]["case_ref"]
    assert result["correspondence"]["body_private_gated"] is True
    operator_language = result["operator_language"]
    assert operator_language["target_language"] == "uk"
    assert operator_language["detected_source_language"]["language_code"] == "en"
    assert "підтвердити отримання" in operator_language["summary_uk"]
    assert "важлива людська кореспонденція" in operator_language["explanation_uk"]["meaning"]


def test_deadline_extraction_creates_exactly_one_scheduler_checkpoint_across_replay(
    tmp_path,
) -> None:
    event = _event()
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    now = int(datetime(2026, 8, 9, 11, 0, tzinfo=timezone.utc).timestamp())

    first = process_important_mail_event(event, scheduler_store=store, now=now)
    second = process_important_mail_event(event, scheduler_store=store, now=now + 60)

    assert len(first["scheduler_checkpoints"]) == 1
    assert len(second["scheduler_checkpoints"]) == 1
    schedule_id = first["scheduler_checkpoints"][0]["schedule_id"]
    assert store.occurrence_count(schedule_id) == 1
    assert first["scheduler_checkpoints"][0]["occurrence_created"] is True
    assert second["scheduler_checkpoints"][0]["occurrence_created"] is False
    assert second["scheduler_checkpoints"][0]["schedule_created"] is False


def test_telegram_contract_contains_reply_callback_and_no_routine_technical_noise(
    tmp_path,
) -> None:
    result, _store = _flow(tmp_path)
    contract = result["telegram_contract"]

    assert contract["actions"] == [
        {
            "label": "Підготувати відповідь",
            "callback": {
                "schema": CALLBACK_SCHEMA,
                "action": "prepare_reply",
                "case_ref": result["case"]["case_ref"],
                "correspondence_ref": result["correspondence"]["correspondence_ref"],
                "draft_semantic_revision": 1,
                "draft_semantic_hash": result["semantic_draft"]["semantic_hash"],
            },
        }
    ]
    rendered = json.dumps(contract, ensure_ascii=False, sort_keys=True)
    assert "traceback" not in rendered.lower()
    assert "provider" not in rendered.lower()
    assert "from_address" not in rendered
    assert PRIVATE_MARKER not in rendered


def test_semantic_draft_revision_and_hash_survive_translation_and_render_stages(
    tmp_path,
) -> None:
    result, _store = _flow(tmp_path)
    render = render_source_language_reply_prompt(result, source_language="en")

    assert result["semantic_draft"]["revision"] == 1
    assert result["correspondence"]["semantic_draft_revision"] == 1
    assert result["telegram_contract"]["actions"][0]["callback"]["draft_semantic_hash"] == (
        result["semantic_draft"]["semantic_hash"]
    )
    assert render["draft_semantic_revision"] == result["semantic_draft"]["revision"]
    assert render["draft_semantic_hash"] == result["semantic_draft"]["semantic_hash"]
    assert render["meaning_locked"] is True


def test_low_confidence_and_high_risk_escalation_is_policy_metadata_only(
    tmp_path,
) -> None:
    result, _store = _flow(
        tmp_path,
        _event(
            subject="!!!",
            body_text=f"!!! {PRIVATE_MARKER}",
            importance="important",
            deadline_at="2026-08-10T09:00:00Z",
        ),
    )

    escalation = result["policy_metadata"]["escalation"]
    assert escalation["required"] is True
    assert escalation["metadata_only"] is True
    assert escalation["private_context_gated"] is True
    assert "low_language_confidence" in escalation["reasons"]
    assert result["correspondence"]["body_included"] is False


def test_high_risk_escalation_is_policy_metadata_only(tmp_path) -> None:
    result, _store = _flow(
        tmp_path,
        _event(body_text=f"Urgent legal deadline on 2026-08-12. {PRIVATE_MARKER}"),
    )

    escalation = result["policy_metadata"]["escalation"]
    assert escalation["required"] is True
    assert escalation["metadata_only"] is True
    assert "high_correspondence_risk" in escalation["reasons"]
    assert result["telegram_contract"]["private_context_available"] is False


def test_public_receipts_contain_no_synthetic_private_marker_values(tmp_path) -> None:
    result, _store = _flow(tmp_path)

    public_rendered = json.dumps(result["public_receipt"], ensure_ascii=False, sort_keys=True)
    assert PRIVATE_MARKER not in public_rendered
    assert "synthetic.client@example.invalid" not in public_rendered
    assert result["public_receipt"]["public_safe"] is True
    assert result["public_receipt"]["private_payloads_included"] is False


def test_rejects_non_synthetic_provider(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")

    with pytest.raises(ValueError, match="synthetic"):
        process_important_mail_event(
            _event(provider="gmail", synthetic_fixture=False),
            scheduler_store=store,
            now=1,
        )
