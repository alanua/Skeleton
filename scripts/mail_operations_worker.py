#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.mail_provider import MailProviderAccount, StaticMailProvider
from core.mail_runtime import MailRuntime, build_mail_dispatcher
from core.mail_state import MailStateStore
from core.scheduler_engine import SchedulerEngine, SchedulerEngineConfig
from core.scheduler_store import SchedulerStore
from integrations.mail_scheduler import build_mail_poll_schedule


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Skeleton Mail Operations worker")
    parser.add_argument("--scheduler-db", default="/var/lib/skeleton/scheduler/scheduler.sqlite3")
    parser.add_argument("--mail-state-db", default="/var/lib/skeleton/mail/mail.sqlite3")
    parser.add_argument("--account", type=Path, required=True)
    parser.add_argument("--provider-fixture", type=Path)
    parser.add_argument("--now", type=int)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("register")
    tick = subparsers.add_parser("tick")
    tick.add_argument("--max-dispatches", type=int, default=8)
    return parser


def main() -> int:
    args = _parser().parse_args()
    now = int(time.time()) if args.now is None else args.now
    try:
        account = MailProviderAccount.from_mapping(
            json.loads(args.account.read_text(encoding="utf-8"))
        )
        scheduler_store = SchedulerStore(args.scheduler_db)
        scheduler_store.initialize()
        if args.command == "register":
            schedule = build_mail_poll_schedule(account)
            record, created = scheduler_store.register(schedule, now=now, enabled=True)
            receipt = {
                "schema": "skeleton.mail_worker_registration_receipt.v1",
                "status": "REGISTERED" if created else "REPLAY",
                "schedule_id": record.spec.schedule_id,
                "version": record.version,
                "public_safe": True,
                "private_payloads_included": False,
                "external_side_effects_executed": False,
            }
        else:
            runtime = MailRuntime(
                state_store=MailStateStore(args.mail_state_db),
                providers={account.provider: _provider(account.provider, args.provider_fixture)},
                clock=lambda: now,
            )
            config = SchedulerEngineConfig(max_dispatches_per_tick=args.max_dispatches)
            receipt = SchedulerEngine(scheduler_store, config).tick(
                now=now,
                dispatcher=build_mail_dispatcher(runtime),
            )
        print(json.dumps(receipt, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        reason = getattr(exc, "reason_code", None) or exc.__class__.__name__
        print(
            json.dumps(
                {
                    "schema": "skeleton.mail_worker_error_receipt.v1",
                    "status": "BLOCKED",
                    "reason": reason,
                    "public_safe": True,
                    "private_payloads_included": False,
                    "external_side_effects_executed": False,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2


def _provider(provider: str, fixture: Path | None) -> StaticMailProvider:
    if fixture is None:
        return StaticMailProvider(())
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    messages = raw.get("messages", raw) if isinstance(raw, dict) else raw
    return StaticMailProvider(messages)


if __name__ == "__main__":
    raise SystemExit(main())
