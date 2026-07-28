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

from core.scheduler_engine import SchedulerEngine, SchedulerEngineConfig
from core.scheduler_models import ScheduleSpec, SchedulerValidationError
from core.scheduler_store import SchedulerStore, SchedulerStoreError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Skeleton Scheduler Core v1")
    parser.add_argument(
        "--db",
        default="/var/lib/skeleton/scheduler/scheduler.sqlite3",
        help="private scheduler SQLite path",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    tick = subparsers.add_parser("tick")
    tick.add_argument("--now", type=int)
    tick.add_argument("--lookback-seconds", type=int, default=24 * 60 * 60)
    tick.add_argument("--misfire-grace-seconds", type=int, default=120)
    tick.add_argument("--stale-running-seconds", type=int, default=60 * 60)

    register = subparsers.add_parser("register")
    register.add_argument("file", type=Path)
    register.add_argument("--disabled", action="store_true")
    register.add_argument("--now", type=int)

    for command in ("pause", "resume"):
        action = subparsers.add_parser(command)
        action.add_argument("schedule_id")

    status = subparsers.add_parser("status")
    status.add_argument("--schedule-id")
    return parser


def main() -> int:
    args = _parser().parse_args()
    store = SchedulerStore(args.db)
    try:
        store.initialize()
        if args.command == "tick":
            config = SchedulerEngineConfig(
                max_lookback_seconds=args.lookback_seconds,
                misfire_grace_seconds=args.misfire_grace_seconds,
                stale_running_after_seconds=args.stale_running_seconds,
            )
            receipt = SchedulerEngine(store, config).tick(now=args.now)
        elif args.command == "register":
            raw = json.loads(args.file.read_text(encoding="utf-8"))
            spec = ScheduleSpec.from_mapping(raw)
            record, created = store.register(
                spec,
                now=int(time.time()) if args.now is None else args.now,
                enabled=not args.disabled,
            )
            receipt = {
                "schema": "skeleton.scheduler_registration_receipt.v1",
                "status": "REGISTERED" if created else "REPLAY",
                "schedule_id": record.spec.schedule_id,
                "version": record.version,
                "enabled": record.enabled,
                "public_safe": True,
                "private_payload_included": False,
            }
        elif args.command in {"pause", "resume"}:
            record = store.set_enabled(args.schedule_id, args.command == "resume")
            receipt = {
                "schema": "skeleton.scheduler_control_receipt.v1",
                "status": "ENABLED" if record.enabled else "PAUSED",
                "schedule_id": record.spec.schedule_id,
                "version": record.version,
                "public_safe": True,
            }
        else:
            counts = store.status_counts()
            receipt = {
                "schema": "skeleton.scheduler_status_receipt.v1",
                "status": "READY",
                **counts,
                "public_safe": True,
                "private_payloads_included": False,
            }
            if args.schedule_id is not None:
                receipt["schedule_occurrences"] = store.occurrence_count(args.schedule_id)
        print(json.dumps(receipt, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, json.JSONDecodeError, SchedulerValidationError, SchedulerStoreError, ValueError) as exc:
        reason = getattr(exc, "reason_code", None) or str(exc)
        print(
            json.dumps(
                {
                    "schema": "skeleton.scheduler_error_receipt.v1",
                    "status": "BLOCKED",
                    "reason": reason,
                    "public_safe": True,
                    "private_payloads_included": False,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
