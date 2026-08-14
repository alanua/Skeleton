from __future__ import annotations

from core.mail_provider import MailProviderAccount, StaticMailProvider
from core.mail_runtime import MailRuntime, build_mail_dispatcher, build_mail_poll_payload
from core.mail_state import MailStateStore
from core.scheduler_engine import SchedulerEngine, SchedulerEngineConfig
from core.scheduler_store import SchedulerStore
from integrations.mail_scheduler import build_mail_poll_schedule


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
