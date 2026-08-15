from __future__ import annotations

from core.mail_provider import MailProviderAccount
from integrations.mail_scheduler import build_mail_poll_schedule


def test_mail_poll_schedule_uses_scheduler_authority_and_mail_route() -> None:
    account = MailProviderAccount.from_mapping(
        {
            "schema": "skeleton.mail_provider_account.v1",
            "account_ref": "acct:gmail-primary",
            "provider": "gmail",
            "poll_interval_seconds": 300,
            "max_messages_per_poll": 10,
            "query": "label:important",
        }
    )

    schedule = build_mail_poll_schedule(account)

    assert schedule.schedule_id == "mail.poll.acct:gmail-primary"
    assert schedule.trigger_kind == "cron"
    assert schedule.route_type == "workflow"
    assert schedule.route_id == "mail.poll_provider"
    assert schedule.approval_policy == "auto_run_low_risk"
    assert schedule.payload["task_packet"]["account"]["provider"] == "gmail"
    assert schedule.payload["approved_capabilities"] == ("mail:poll",)
