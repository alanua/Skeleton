#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.family_document_intake import MfpScanSessionAssembler
from core.family_document_runtime import FamilyDocumentRuntime, private_repair_handoff
from core.family_document_sources import iter_configured_artifacts, load_source_profiles


def main() -> int:
    parser = argparse.ArgumentParser(description="Skeleton family document MFP intake worker")
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--now", type=float, default=None)
    parser.add_argument("--recover-stale", action="store_true")
    args = parser.parse_args()

    runtime = FamilyDocumentRuntime.open(args.runtime_root)
    assembler = MfpScanSessionAssembler(runtime)
    profiles = load_source_profiles(args.config)
    receipts: list[dict[str, object]] = []
    now = args.now
    for profile, artifact in iter_configured_artifacts(profiles):
        discovered_at = now if now is not None else artifact.stat().st_mtime
        receipts.append(dict(assembler.ingest(artifact, profile, discovered_at=discovered_at)))
    if args.recover_stale:
        receipts.extend(assembler.recover_stale_sessions(now=now or 0))
    print(json.dumps({
        "schema": "skeleton.family_document_worker_receipt.v1",
        "status": "DONE",
        "receipt_count": len(receipts),
        "private_values_in_public_receipt": False,
    }, sort_keys=True))
    return 0


__all__ = ["main", "private_repair_handoff"]


if __name__ == "__main__":
    raise SystemExit(main())
