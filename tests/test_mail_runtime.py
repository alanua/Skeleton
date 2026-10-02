from __future__ import annotations

import json

import pytest

from core.mail_operations import MailOperationError
from core.mail_provider import MailProviderAccount, MailProviderBatch, MailProviderCursor, StaticMailProvider
from core.mail_runtime import MailRuntime, build_mail_dispatcher, build_mail_poll_payload
from core.mail_state import MailStateStore
from core.scheduler_engine import SchedulerEngine, SchedulerEngineConfig
from core.scheduler_store import SchedulerStore
from integrations.mail_scheduler import build_mail_poll_schedule
from scripts import mail_operations_worker


def _account() -> MailProviderAccount:
    return MailProviderAccount.from_mapping(
        {
            "schema": "skeleton.mail_provider_account.v1",
            "account_ref": "acct:static",
            "provider": "static",
            "poll_interval_seconds": 60,
            "max_messages_per_poll": 10,
            "query": "synthetic",
        }
    )


def _gmail_account() -> MailProviderAccount:
    return MailProviderAccount.from_mapping(
        {
            "schema": "skeleton.mail_provider_account.v1",
            "account_ref": "acct:gmail-primary",
            "provider": "gmail",
            "poll_interval_seconds": 300,
            "max_messages_per_poll": 10,
            "query": "newer_than:7d",
        }
    )


def _message(**updates):
    value = {
        "provider": "static",
        "provider_message_ref": "msg-1",
        "thread_ref": "thread-1",
        "sender_ref": "sender-ref",
        "received_at": 1786400000,
        "subject_hint": "Technical incident",
        "body_preview": "Important outage deadline 2026-09-01",
        "importance_hint": None,
        "deadline_hint": "2026-09-01",
    }
    value.update(updates)
    return value


def test_mail_runtime_is_idempotent_across_replayed_polls(tmp_path) -> None:
    account = _account()
    runtime = MailRuntime(
        state_store=MailStateStore(tmp_path / "mail.sqlite3"),
        providers={"static": StaticMailProvider([_message()])},
        clock=lambda: 1786400010,
    )
    payload = build_mail_poll_payload(account)["task_packet"]

    first = runtime.process_poll_packet(payload)
    second = runtime.process_poll_packet(payload)

    assert first["processed"] == 1
    assert first["needs_operator"] == 1
    assert second["processed"] == 0
    assert second["replayed"] == 1
    assert first["message_receipts"][0]["operator_packet"]["policy"]["category"] == "technical"
    pending = runtime.state_store.pending_lifecycle_work()
    assert pending[0]["state"] == "BLOCKED"
    assert pending[0]["next_action"] == "operator_review_action_required_mail"


def test_scheduler_dispatches_mail_poll_route_without_second_authority(tmp_path) -> None:
    account = _account()
    scheduler_store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    scheduler_store.initialize()
    scheduler_store.register(build_mail_poll_schedule(account), now=100)
    runtime = MailRuntime(
        state_store=MailStateStore(tmp_path / "mail.sqlite3"),
        providers={"static": StaticMailProvider([_message(provider_message_ref="msg-2")])},
        clock=lambda: 120,
    )

    receipt = SchedulerEngine(
        scheduler_store,
        SchedulerEngineConfig(max_dispatches_per_tick=4),
    ).tick(now=120, dispatcher=build_mail_dispatcher(runtime))

    assert receipt["dispatch"]["done"] == 1
    occurrences = scheduler_store.list_occurrences("mail.poll.acct:static")
    assert [item.state for item in occurrences].count("done") == 1


def test_production_gmail_missing_registered_credential_does_not_fall_back(monkeypatch) -> None:
    account = _gmail_account()

    def blocked(**_kwargs):
        raise RuntimeError("registered credential missing")

    monkeypatch.setattr(mail_operations_worker, "build_registered_gmail_provider", blocked)
    with pytest.raises(RuntimeError, match="registered credential missing"):
        mail_operations_worker._provider(account, None)


def test_explicit_fixture_mode_remains_offline_and_deterministic(tmp_path) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"messages": [_message(provider="gmail")]}), encoding="utf-8")

    provider = mail_operations_worker._provider(_gmail_account(), fixture)

    assert isinstance(provider, StaticMailProvider)


def test_gmail_oauth_reauthorization_blocks_and_preserves_checkpoint(tmp_path) -> None:
    class RevokedGmailProvider:
        provider = "gmail"

        def poll(self, account: MailProviderAccount, cursor: MailProviderCursor, *, max_messages: int):
            raise MailOperationError("GMAIL_OAUTH_REVOKED", "reauthorization required")

    account = _gmail_account()
    state = MailStateStore(tmp_path / "mail.sqlite3")
    state.initialize()
    state.update_cursor(
        account_ref=account.account_ref,
        provider="gmail",
        cursor_ref="gmail-cursor-1",
        now=100,
    )
    runtime = MailRuntime(
        state_store=state,
        providers={"gmail": RevokedGmailProvider()},
        clock=lambda: 120,
    )

    receipt = runtime.process_poll_packet(build_mail_poll_payload(account)["task_packet"])

    assert receipt["status"] == "BLOCKED"
    assert receipt["reason"] == "GMAIL_OAUTH_REAUTHORIZATION_REQUIRED"
    assert receipt["resume_from_cursor_ref"] == "gmail-cursor-1"
    assert state.get_cursor(account.account_ref) == "gmail-cursor-1"
    pending = state.pending_lifecycle_work()
    assert pending[0]["state"] == "BLOCKED"
    assert pending[0]["next_action"] == "operator_reauthorize_gmail_oauth_then_resume_checkpoint"


def test_mail_resume_after_authorization_uses_existing_cursor(tmp_path) -> None:
    account = _gmail_account()
    state = MailStateStore(tmp_path / "mail.sqlite3")
    state.initialize()
    state.update_cursor(
        account_ref=account.account_ref,
        provider="gmail",
        cursor_ref="gmail-cursor-1",
        now=100,
    )
    runtime = MailRuntime(
        state_store=state,
        providers={"gmail": StaticMailProvider([_message(provider="gmail", provider_message_ref="gmail-msg-2")])},
        clock=lambda: 130,
    )

    receipt = runtime.process_poll_packet(build_mail_poll_payload(account)["task_packet"])

    assert receipt["status"] == "DONE"
    assert receipt["processed"] == 1
    assert state.get_cursor(account.account_ref) == "gmail-msg-2"
