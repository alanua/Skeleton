import json
from pathlib import Path

import pytest

from core.mail_provider import (
    MailCleanupAction,
    MailProviderAccount,
    MailProviderError,
    MailChange,
    ProviderMailMessage,
    ProviderScanResult,
)
from core.mail_runtime import MailRuntime
from core.mail_state import MailRuntimeState
from integrations.mail_scheduler import InMemoryMailScheduler
from integrations.mail_telegram import RecordingMailTelegramEmitter


class FakeProvider:
    provider_name = "gmail"

    def __init__(self, messages):
        self.messages = {message.provider_message_ref: message for message in messages}
        self.cleanup_calls = []

    def scan(self, account, *, cursor, limit):
        account.require_secret()
        changes = tuple(MailChange(ref, None, 1786400010) for ref in self.messages)
        return ProviderScanResult("cursor-after-scan", changes)

    def fetch_message(self, account, *, provider_message_ref):
        return self.messages[provider_message_ref]

    def apply_cleanup(self, account, *, actions):
        self.cleanup_calls.extend(actions)
        return {"status": "DONE", "reason": "CLEANUP_APPLIED", "changed": len(actions)}

    def send_message(self, *_args, **_kwargs):
        raise MailProviderError("SEND_DISABLED", "send disabled")


def _account(**updates):
    packet = {
        "schema": "skeleton.mail_provider.account.v1",
        "provider": "gmail",
        "alias": "primary",
        "credential_ref": {"kind": "env", "name": "TOKEN_ENV"},
        "poll_interval_seconds": 300,
        "cleanup_enabled": False,
    }
    packet.update(updates)
    return MailProviderAccount.from_mapping(packet)


def _message(**updates):
    packet = {
        "provider": "gmail",
        "provider_message_ref": "msg-1",
        "provider_thread_ref": "thread-1",
        "received_at": 1786400000,
        "subject": "Important deadline",
        "body_preview": "Action required by 2026-09-01.",
        "sender": "sender@example.invalid",
        "labels": ("IMPORTANT",),
        "headers": {},
    }
    packet.update(updates)
    return ProviderMailMessage(**packet)


def _runtime(tmp_path: Path, messages, *, account=None):
    scheduler = InMemoryMailScheduler()
    telegram = RecordingMailTelegramEmitter()
    provider = FakeProvider(messages)
    runtime = MailRuntime(
        state=MailRuntimeState(tmp_path / "mail.sqlite3"),
        provider=provider,
        account=account or _account(),
        scheduler=scheduler,
        telegram=telegram,
    )
    return runtime, provider, scheduler, telegram


def test_duplicate_scan_creates_no_duplicate_case_deadline_or_action(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_ENV", "token")
    runtime, _provider, scheduler, telegram = _runtime(tmp_path, [_message()])

    first = runtime.scan_once(now=1786400010)
    second = runtime.scan_once(now=1786400020)

    assert first["scan_counts"]["processed"] == 1
    assert second["scan_counts"]["duplicate"] == 1
    assert len(scheduler.checkpoints) == 1
    assert len(telegram.packets) == 1
    assert runtime.state.counts()["message_handoffs"] == 1


def test_restart_resumes_from_durable_cursor_and_dedupe_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_ENV", "token")
    first, _provider, _scheduler, _telegram = _runtime(tmp_path, [_message()])
    first.scan_once(now=1786400010)

    restarted, _provider2, scheduler2, telegram2 = _runtime(tmp_path, [_message()])
    receipt = restarted.scan_once(now=1786400090)

    assert receipt["scan_counts"]["duplicate"] == 1
    assert restarted.state.get_cursor("gmail:primary") == "cursor-after-scan"
    assert len(scheduler2.checkpoints) == 0
    assert len(telegram2.packets) == 0


def test_important_mail_produces_one_actionable_operator_packet(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_ENV", "token")
    runtime, _provider, _scheduler, telegram = _runtime(tmp_path, [_message()])

    runtime.scan_once(now=1786400010)

    assert len(telegram.packets) == 1
    _packet_ref, packet = telegram.packets[0]
    assert packet["telegram_reply_contract"]["actionable"] is True
    rendered = json.dumps(runtime.scan_once(now=1786400020), sort_keys=True)
    assert "sender@example.invalid" not in rendered
    assert "Important deadline" not in rendered


def test_deadline_produces_exactly_one_scheduler_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_ENV", "token")
    runtime, _provider, scheduler, _telegram = _runtime(tmp_path, [_message()])

    runtime.scan_once(now=1786400010)

    assert len(scheduler.checkpoints) == 1
    checkpoint = next(iter(scheduler.checkpoints.values()))
    assert checkpoint["route_id"] == "mail.operator_checkpoint"


def test_github_technical_notification_requires_correlation_before_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_ENV", "token")
    correlated = _message(
        provider_message_ref="github-1",
        subject="GitHub workflow succeeded alanua/Skeleton",
        body_preview="Run 123456 completed for 0123456789abcdef0123456789abcdef01234567",
        sender="notifications@github.com",
        labels=(),
    )
    uncorrelated = _message(
        provider_message_ref="github-2",
        subject="GitHub workflow succeeded",
        body_preview="Completed without authority refs",
        sender="notifications@github.com",
        labels=(),
    )
    runtime, provider, _scheduler, telegram = _runtime(
        tmp_path,
        [correlated, uncorrelated],
        account=_account(cleanup_enabled=True, label_after_handoff="skeleton-handoff"),
    )

    runtime.scan_once(now=1786400010)

    assert len(provider.cleanup_calls) == 1
    assert isinstance(provider.cleanup_calls[0], MailCleanupAction)
    assert provider.cleanup_calls[0].provider_message_ref == "github-1"
    assert len(telegram.packets) == 0


def test_send_path_is_unavailable_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_ENV", "token")
    runtime, _provider, _scheduler, _telegram = _runtime(tmp_path, [_message()])

    with pytest.raises(MailProviderError) as error:
        runtime.send_message({"body": "private"})
    assert error.value.reason_code == "SEND_DISABLED"


def test_auth_missing_returns_bounded_auth_required_without_secret_leakage(tmp_path, monkeypatch):
    monkeypatch.delenv("TOKEN_ENV", raising=False)
    runtime, _provider, _scheduler, _telegram = _runtime(tmp_path, [_message()])

    receipt = runtime.scan_once(now=1786400010)

    rendered = json.dumps(receipt, sort_keys=True)
    assert receipt["status"] == "AUTH_REQUIRED"
    assert "TOKEN_ENV" not in rendered
    assert "sender@example.invalid" not in rendered
    assert receipt["provider_alias"] == "gmail:primary"
