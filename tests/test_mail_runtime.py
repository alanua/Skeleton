import json

from adapters.gmail_mail_provider import GmailMailProvider
from core.mail_provider import MailProviderAccount
from core.mail_runtime import MailRuntime, MailRuntimeConfig, health_canary
from core.mail_state import MailStateStore
from core.scheduler_store import SchedulerStore


def _account(secret: bool = True) -> MailProviderAccount:
    return MailProviderAccount.from_mapping(
        {
            "schema": "skeleton.mail_provider.account.v1",
            "account_ref": "acct.gmail.primary",
            "provider": "gmail",
            "secret_reference": (
                {"ref": "secretref:gmail-primary", "kind": "oauth_refresh_token"} if secret else None
            ),
        }
    )


def _config(secret: bool = True) -> MailRuntimeConfig:
    return MailRuntimeConfig(accounts=(_account(secret),))


def _transport(_account, _cursor, _now):
    return {
        "status": "OK",
        "next_cursor": "history-2",
        "messages": [
            {
                "id": "gmail-message-private-id",
                "thread_id": "gmail-thread-private-id",
                "history_id": "history-1",
                "received_at": 1786400000,
                "subject": "Important deadline private subject",
                "body_preview": "Action required by 2026-09-01 with private customer words",
                "sender": "person@example.invalid",
                "labels": ["IMPORTANT"],
            }
        ],
    }


def test_runtime_deduplicates_message_deadline_and_operator_packet(tmp_path) -> None:
    state = MailStateStore(tmp_path / "mail.sqlite3")
    scheduler = SchedulerStore(tmp_path / "scheduler.sqlite3")
    runtime = MailRuntime(
        state=state,
        scheduler=scheduler,
        providers={"gmail": GmailMailProvider(transport=_transport)},
    )

    first = runtime.poll_once(_config(), now=1786400010)
    second = runtime.poll_once(_config(), now=1786400020)

    assert first["aggregate_counts"]["new_messages"] == 1
    assert first["aggregate_counts"]["operator_packets"] == 1
    assert first["aggregate_counts"]["scheduler_checkpoints"] == 1
    assert second["aggregate_counts"]["duplicate_messages"] == 1
    assert second["aggregate_counts"]["operator_packets"] == 0
    assert state.aggregate_counts()["processed_messages"] == 1
    assert state.aggregate_counts()["operator_packets"] == 1
    assert state.aggregate_counts()["scheduler_deadlines"] == 1
    assert scheduler.occurrence_count("mail.deadline." + first["state_counts"]["processed_messages"].__str__()) == 0


def test_runtime_receipt_is_public_safe_and_does_not_leak_mail_values(tmp_path) -> None:
    runtime = MailRuntime(
        state=MailStateStore(tmp_path / "mail.sqlite3"),
        scheduler=SchedulerStore(tmp_path / "scheduler.sqlite3"),
        providers={"gmail": GmailMailProvider(transport=_transport)},
    )

    receipt = runtime.poll_once(_config(), now=1786400010)
    encoded = json.dumps(receipt, sort_keys=True)

    assert receipt["public_safe"] is True
    assert receipt["private_payloads_included"] is False
    assert "gmail-message-private-id" not in encoded
    assert "private subject" not in encoded
    assert "person@example.invalid" not in encoded


def test_missing_auth_fails_closed_with_bounded_auth_required(tmp_path) -> None:
    runtime = MailRuntime(
        state=MailStateStore(tmp_path / "mail.sqlite3"),
        scheduler=SchedulerStore(tmp_path / "scheduler.sqlite3"),
        providers={"gmail": GmailMailProvider(transport=_transport)},
    )

    receipt = runtime.poll_once(_config(secret=False), now=1786400010)

    assert receipt["status"] == "AUTH_REQUIRED"
    assert receipt["aggregate_counts"]["auth_required"] == 1
    assert receipt["aggregate_counts"]["polled_messages"] == 0
    assert receipt["external_side_effects_executed"] is False


def test_invoice_route_is_private_ref_only(tmp_path) -> None:
    def transport(_account, _cursor, _now):
        result = _transport(_account, _cursor, _now)
        result["messages"][0]["subject"] = "Invoice deadline"
        result["messages"][0]["attachment_refs"] = ["att:invoice-local-private"]
        return result

    runtime = MailRuntime(
        state=MailStateStore(tmp_path / "mail.sqlite3"),
        scheduler=SchedulerStore(tmp_path / "scheduler.sqlite3"),
        providers={"gmail": GmailMailProvider(transport=transport)},
    )

    receipt = runtime.poll_once(_config(), now=1786400010)

    assert receipt["aggregate_counts"]["private_routes"] == 1
    assert runtime.state.aggregate_counts()["private_routes"] == 1


def test_health_canary_is_aggregate_only(tmp_path) -> None:
    receipt = health_canary(MailStateStore(tmp_path / "mail.sqlite3"))

    assert receipt["status"] == "READY"
    assert receipt["private_payloads_included"] is False
    assert "aggregate_counts" in receipt
