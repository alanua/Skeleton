#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.family_document_runtime import FamilyDocumentReceiptOutbox, FamilyDocumentRuntime
from core.family_document_sinks import FileFamilyDocumentArchive
from core.family_document_sources import LocalDirectoryDocumentSource


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one local family-document intake scan.")
    parser.add_argument("--inbox", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--outbox-db", required=True)
    parser.add_argument("--no-drain", action="store_true")
    args = parser.parse_args()

    runtime = FamilyDocumentRuntime(
        source=LocalDirectoryDocumentSource(Path(args.inbox)),
        archive_sink=FileFamilyDocumentArchive(Path(args.archive)),
        outbox=FamilyDocumentReceiptOutbox(Path(args.outbox_db)),
    )
    print(json.dumps(runtime.scan_once(drain=not args.no_drain), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
