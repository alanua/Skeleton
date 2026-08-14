#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.family_document_runtime import DurableJournal, FamilyDocumentWorker, RuntimeLimits
from scripts.family_document_intake import load_processor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="family-document-worker")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--worker-id", default="family-document-worker-1")
    args = parser.parse_args(argv)
    payload = json.loads(args.config.expanduser().resolve(strict=True).read_text(encoding="utf-8"))
    processor = load_processor(args.config)
    journal = DurableJournal(
        Path(str(payload["journal_path"])),
        RuntimeLimits(
            settle_seconds=float(payload.get("settle_seconds", 3.0)),
            lease_seconds=int(payload.get("lease_seconds", 300)),
            max_attempts=int(payload.get("max_attempts", 4)),
            retry_base_seconds=int(payload.get("retry_base_seconds", 30)),
            max_inventory_files=int(payload.get("max_inventory_files", 10000)),
        ),
    )
    worker = FamilyDocumentWorker(
        roots=processor.config.approved_roots,
        journal=journal,
        processor=processor,
        lock_path=Path(str(payload["worker_lock_path"])),
        worker_id=args.worker_id,
        receipt_outbox=processor.config.receipt_outbox,
    )
    if args.once:
        print(json.dumps(worker.run_once(), sort_keys=True))
        return 0
    if not 0.5 <= args.poll_seconds <= 300:
        raise SystemExit("poll_interval_invalid")
    while True:
        print(json.dumps(worker.run_once(), sort_keys=True), flush=True)
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
