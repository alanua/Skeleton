from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from core.local_inference_runtime import InferenceQueue
from core.mail_operations import (
    LOCAL_MAIL_CLASSIFIER_MODEL,
    LOCAL_MAIL_CLASSIFIER_REQUEST_TYPE,
    MAIL_GITHUB_CI_EVENT_SCHEMA,
    MAIL_TELEGRAM_REPLY_DRAFT_SCHEMA,
    MailOperationsError,
    MailOperationsStore,
    assert_public_mail_payload,
    build_local_inference_request,
    build_telegram_reply_draft_contract,
    correlate_github_ci_event,
    deadline_to_schedule_spec,
)
from core.scheduler_engine import SchedulerEngine
from core.scheduler_store import SchedulerStore


PRIVATE_ADDRESS = "private" + "@" + "example.test"
PRIVATE_BODY = "local-" + "only-mail-body-sentinel"
PRIVATE_SUBJECT = "local-" + "only-mail-subject-sentinel"


def invoice_envelope(**overrides: object) -> dict[str, object]:
    envelope: dict[str, object] = {
        "provider": "imap",
        "provider_account_ref": "acct-local-1",
        "provider_message_ref": "msg-001",
        "received_at": 1786200000,
        "private_payload": {
            "subject": PRIVATE_SUBJECT,
            "body": PRIVATE_BODY,
            "from": PRIVATE_ADDRESS,
        },
        "public_signals": {
            "labels": ["invoice"],
            "attachment_kinds": ["invoice_pdf"],
            "retention_years": 10,
            "deadline_epoch": 1786800000,
        },
    }
    envelope.update(overrides)
    return envelope


def github_ci_envelope() -> dict[str, object]:
    return invoice_envelope(
        provider="gmail",
        provider_account_ref="acct-local-ci",
        provider_message_ref="msg-ci-001",
        public_signals={
            "service_identity": "github-actions",
            "repo": "alanua/Skeleton",
            "workflow": "Mail Operations CI",
            "run_id": "run-2266",
            "status": "failure",
            "commit_sha": "a1b2c3d4e5f60718293a4b5c6d7e8f901234abcd",
            "labels": ["ci"],
        },
    )


def assert_no_private_values(value: object) -> None:
    rendered = json.dumps(value, ensure_ascii=True, sort_keys=True)
    assert PRIVATE_BODY not in rendered
    assert PRIVATE_SUBJECT not in rendered
    assert PRIVATE_ADDRESS not in rendered
    assert "body" not in rendered
    assert "subject" not in rendered
    assert "from" not in rendered


def test_read_only_ingest_classifies_indexes_deadline_and_is_idempotent(tmp_path: Path) -> None:
    store = MailOperationsStore(tmp_path / "mail.sqlite3")

    first = store.ingest_batch([invoice_envelope()], now=1786200100)
    second = store.ingest_batch([invoice_envelope()], now=1786200200)

    assert first["created_cases"] == 1
    assert first["created_index_entries"] == 2
    assert first["created_deadlines"] == 1
    assert len(first["followup_tasks"]) == 3
    assert second["created_cases"] == 0
    assert second["replayed_cases"] == 1
    assert second["created_index_entries"] == 0
    assert second["created_deadlines"] == 0
    assert store.counts() == {"cases": 1, "index_entries": 2, "deadlines": 1}
    assert_no_private_values(first)
    assert_no_private_values(second)

    with sqlite3.connect(tmp_path / "mail.sqlite3") as connection:
        public_rows = connection.execute(
            "SELECT public_json FROM mail_cases UNION ALL "
            "SELECT public_json FROM mail_index_entries UNION ALL "
            "SELECT public_json FROM mail_deadlines"
        ).fetchall()
    assert_no_private_values(public_rows)


def test_local_first_model_routing_uses_hash_reference_and_queue_idempotency(tmp_path: Path) -> None:
    request = build_local_inference_request(invoice_envelope())
    queue = InferenceQueue(tmp_path / "inference")

    request_id, created = queue.submit(
        request_type=str(request["request_type"]),
        model=str(request["model"]),
        payload=request["payload"],  # type: ignore[arg-type]
        idempotency_key=str(request["idempotency_key"]),
        timeout_seconds=30,
    )
    duplicate_id, duplicate_created = queue.submit(
        request_type=str(request["request_type"]),
        model=str(request["model"]),
        payload=request["payload"],  # type: ignore[arg-type]
        idempotency_key=str(request["idempotency_key"]),
        timeout_seconds=30,
    )

    assert request["request_type"] == LOCAL_MAIL_CLASSIFIER_REQUEST_TYPE
    assert request["model"] == LOCAL_MAIL_CLASSIFIER_MODEL
    assert request["payload"]["content_ref_hash"]  # type: ignore[index]
    assert request["payload"]["privacy_boundary"] == "PRIVATE_EMAIL_CONTENT_LOCAL_ONLY"  # type: ignore[index]
    assert (request_id, created) == (duplicate_id, True)
    assert duplicate_created is False
    assert queue.status()["counts"]["pending"] == 1
    assert_no_private_values(request)


def test_github_ci_event_correlation_is_public_safe_and_service_bound() -> None:
    event = correlate_github_ci_event(github_ci_envelope())

    assert event is not None
    assert event["schema"] == MAIL_GITHUB_CI_EVENT_SCHEMA
    assert event["service_identity"] == "github-actions"
    assert event["repo"] == "alanua/Skeleton"
    assert event["run_id"] == "run-2266"
    assert event["commit_sha"] == "a1b2c3d4e5f60718293a4b5c6d7e8f901234abcd"
    assert_no_private_values(event)
    assert_public_mail_payload(event)


def test_telegram_reply_draft_contract_exposes_only_local_reference() -> None:
    contract = build_telegram_reply_draft_contract(
        case_id="mailcase_abc123",
        local_draft_ref="draft_local_001",
        actor_reference="telegram:user-42",
        reason_code="OPERATOR_REVIEW",
    )

    assert contract["schema"] == MAIL_TELEGRAM_REPLY_DRAFT_SCHEMA
    assert contract["allowed_actions"] == ("approve_send", "revise_local", "discard_local")
    assert contract["requires_local_private_mail_client"] is True
    assert contract["external_side_effects_executed"] is False
    assert_no_private_values(contract)


def test_scheduler_deadline_linkage_is_idempotent(tmp_path: Path) -> None:
    mail_store = MailOperationsStore(tmp_path / "mail.sqlite3")
    mail_store.ingest_batch([invoice_envelope()], now=1786200100)
    deadline = mail_store.list_deadline_records()[0]
    spec = deadline_to_schedule_spec(deadline)
    scheduler_store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    scheduler_store.initialize()

    first_schedule, created = scheduler_store.register(spec, now=1786200200)
    replay_schedule, replay_created = scheduler_store.register(spec, now=1786200300)
    first_tick = SchedulerEngine(scheduler_store).tick(now=1786800000)
    second_tick = SchedulerEngine(scheduler_store).tick(now=1786800000)

    assert first_schedule.version == replay_schedule.version == 1
    assert created is True
    assert replay_created is False
    assert first_tick["created_occurrences"] == 1
    assert second_tick["created_occurrences"] == 0
    assert scheduler_store.occurrence_count(spec.schedule_id) == 1
    assert_no_private_values(spec.to_mapping())
    assert_no_private_values(first_tick)


def test_public_signal_email_addresses_are_rejected() -> None:
    envelope = invoice_envelope(
        public_signals={
            "labels": ["invoice"],
            "service_identity": "private-person" + "@" + "example.test",
        }
    )

    with pytest.raises(MailOperationsError) as caught:
        MailOperationsStore(":memory:").ingest_batch([envelope], now=1786200100)

    assert caught.value.reason_code == "PRIVATE_EMAIL_ADDRESS_IN_PUBLIC_PAYLOAD"
