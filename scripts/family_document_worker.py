#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time

from core.home_edge.family_document_production import (
    build_family_document_production_runtime,
    config_from_args,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the family-document intake worker.")
    parser.add_argument("--inbox", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--outbox-db", required=True)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    runtime = build_family_document_production_runtime(
        config_from_args(inbox=args.inbox, archive=args.archive, outbox_db=args.outbox_db)
    )
    while True:
        print(json.dumps(runtime.scan_once(), sort_keys=True), flush=True)
        if args.once:
            return 0
        time.sleep(max(args.interval_seconds, 1.0))


if __name__ == "__main__":
    raise SystemExit(main())
