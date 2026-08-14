from __future__ import annotations

from core.mail_provider import MailProviderAccount
from core.mail_runtime import MAIL_POLL_ROUTE_ID, MAIL_POLL_ROUTE_TYPE, build_mail_poll_payload
from core.scheduler_models import SCHEDULE_SCHEMA, ScheduleSpec


def build_mail_poll_schedule(account: MailProviderAccount) -> ScheduleSpec:
    minutes = max(1, account.poll_interval_seconds // 60)
    cron = f"*/{minutes} * * * *" if minutes < 60 else "0 * * * *"
    return ScheduleSpec.from_mapping(
        {
            "schema": SCHEDULE_SCHEMA,
            "schedule_id": f"mail.poll.{account.account_ref}",
            "trigger_kind": "cron",
            "cron_expression": cron,
            "once_at": None,
            "timezone": "UTC",
            "route_type": MAIL_POLL_ROUTE_TYPE,
            "route_id": MAIL_POLL_ROUTE_ID,
            "approval_policy": "auto_run_low_risk",
            "overlap_policy": "queue_one",
            "misfire_policy": "run_once",
            "payload": build_mail_poll_payload(account),
        }
    )
