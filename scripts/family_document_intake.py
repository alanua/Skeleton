#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from core.home_edge.family_document_production import (
    build_family_document_production_runtime,
    config_from_args,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one local family-document intake scan.")
    parser.add_argument("--inbox", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--outbox-db", required=True)
    parser.add_argument("--no-drain", action="store_true")
    args = parser.parse_args()

    runtime = build_family_document_production_runtime(
        config_from_args(inbox=args.inbox, archive=args.archive, outbox_db=args.outbox_db)
    )
    print(json.dumps(runtime.scan_once(drain=not args.no_drain), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
