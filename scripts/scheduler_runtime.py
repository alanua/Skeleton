from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.scheduler_engine import SchedulerEngine, SchedulerEngineConfig
from core.scheduler_store import SchedulerStore
from core.shared_dispatch import SharedDispatcher


SCHEDULER_DB_ENV = "SKELETON_SCHEDULER_DB"
LOOP_STATE_DB_ENV = "SKELETON_LOOP_STATE_DB"


def run_scheduler_tick(
    *,
    scheduler_db_path: str,
    loop_state_db_path: str,
    now: int | None = None,
) -> dict[str, object]:
    store = SchedulerStore(scheduler_db_path)
    store.initialize()
    dispatcher = SharedDispatcher.for_loop_engine(loop_state_db_path=loop_state_db_path)
    return SchedulerEngine(store, SchedulerEngineConfig()).tick(
        now=now,
        dispatcher=dispatcher,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one scheduler continuation tick.")
    parser.add_argument("--scheduler-db", default=os.environ.get(SCHEDULER_DB_ENV))
    parser.add_argument("--loop-state-db", default=os.environ.get(LOOP_STATE_DB_ENV))
    parser.add_argument("--now", type=int, default=None)
    args = parser.parse_args()

    if not args.scheduler_db or not args.loop_state_db:
        raise SystemExit("scheduler and loop state DB paths are required")
    receipt = run_scheduler_tick(
        scheduler_db_path=args.scheduler_db,
        loop_state_db_path=args.loop_state_db,
        now=args.now,
    )
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
