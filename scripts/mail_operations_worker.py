#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.gmail_mail_provider import GmailMailProvider
from core.mail_provider import MailProviderAccount
from core.mail_runtime import MailRuntime
from core.mail_state import MailRuntimeState
from integrations.mail_scheduler import SchedulerStoreMailScheduler
from integrations.mail_telegram import EnvTelegramEmitter


DEFAULT_STATE_DB = Path("/home/agent/.local/state/skeleton-runner/mail/mail_runtime.sqlite3")
DEFAULT_SCHEDULER_DB = Path("/home/agent/.local/state/skeleton-runner/scheduler/scheduler.sqlite3")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Skeleton private mail operations worker")
    parser.add_argument("--account", required=True, help="Path to private provider account JSON")
    parser.add_argument("--state-db", default=str(DEFAULT_STATE_DB))
    parser.add_argument("--scheduler-db", default=str(DEFAULT_SCHEDULER_DB))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--readiness", action="store_true")
    return parser


def load_account(path: str) -> MailProviderAccount:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit("account config must be a JSON object")
    return MailProviderAccount.from_mapping(raw)


def build_runtime(args: argparse.Namespace) -> MailRuntime:
    account = load_account(args.account)
    if account.provider != "gmail":
        raise SystemExit("only gmail provider is active; other providers remain pluggable")
    return MailRuntime(
        state=MailRuntimeState(args.state_db),
        provider=GmailMailProvider(),
        account=account,
        scheduler=SchedulerStoreMailScheduler(args.scheduler_db),
        telegram=EnvTelegramEmitter(),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runtime = build_runtime(args)
    if args.health or args.readiness:
        print(json.dumps(runtime.health(), ensure_ascii=True, sort_keys=True))
        return 0
    if args.once:
        print(json.dumps(runtime.scan_once(), ensure_ascii=True, sort_keys=True))
        return 0
    while True:
        receipt = runtime.scan_once()
        print(json.dumps(receipt, ensure_ascii=True, sort_keys=True), flush=True)
        time.sleep(runtime.account.poll_interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
