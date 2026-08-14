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

from adapters.gmail_mail_provider import GmailMailProvider
from core.mail_provider import MailProviderError
from core.mail_runtime import MailRuntime, MailRuntimeConfig, MailRuntimeError, health_canary
from core.mail_state import MailStateStore
from core.scheduler_store import SchedulerStore, SchedulerStoreError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Skeleton Mail Operations worker")
    parser.add_argument("--config", type=Path, required=True, help="private provider account config")
    parser.add_argument("--state-db", required=True, help="private mail runtime SQLite path")
    parser.add_argument("--scheduler-db", required=True, help="existing Scheduler SQLite path")
    parser.add_argument("--now", type=int)
    parser.add_argument("--health", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    state = MailStateStore(args.state_db)
    try:
        if args.health:
            print(_json(health_canary(state)))
            return 0
        raw = json.loads(args.config.read_text(encoding="utf-8"))
        config = MailRuntimeConfig.from_mapping(raw)
        runtime = MailRuntime(
            state=state,
            scheduler=SchedulerStore(args.scheduler_db),
            providers={"gmail": GmailMailProvider()},
        )
        receipt = runtime.poll_once(config, now=int(time.time()) if args.now is None else args.now)
        print(_json(receipt))
        return 0 if receipt["status"] in {"OK", "AUTH_REQUIRED"} else 2
    except (OSError, json.JSONDecodeError, MailProviderError, MailRuntimeError, SchedulerStoreError, ValueError) as exc:
        reason = getattr(exc, "reason_code", None) or "MAIL_WORKER_BLOCKED"
        print(
            _json(
                {
                    "schema": "skeleton.mail_runtime.receipt.v1",
                    "status": "BLOCKED",
                    "reason": reason,
                    "public_safe": True,
                    "private_payloads_included": False,
                    "external_side_effects_executed": False,
                }
            )
        )
        return 2


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


if __name__ == "__main__":
    raise SystemExit(main())
