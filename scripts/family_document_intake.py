#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.family_document_runtime import FamilyDocumentReceiptOutbox, FamilyDocumentRuntime
from core.family_document_sinks import (
    CompositeFamilyDocumentArchive,
    FileFamilyDocumentArchive,
    MemoryGatewayFamilyDocumentArchive,
)
from core.family_document_sources import LocalDirectoryDocumentSource
from core.memory_gateway import MemoryGateway, capability_token
from core.memory_gateway_storage import PrivateMemoryGatewayStorage
from core.private_memory_stack import PrivateMemoryStack


def _archive_sink(archive_root: Path, private_memory_root: Path) -> CompositeFamilyDocumentArchive:
    stack = PrivateMemoryStack(private_memory_root)
    storage = PrivateMemoryGatewayStorage(stack)
    gateway = MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=False),
        private_memory_storage=storage,
    )
    return CompositeFamilyDocumentArchive(
        FileFamilyDocumentArchive(archive_root),
        MemoryGatewayFamilyDocumentArchive(gateway),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one local family-document intake scan.")
    parser.add_argument("--inbox", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--outbox-db", required=True)
    parser.add_argument("--private-memory-root", required=True)
    parser.add_argument("--no-drain", action="store_true")
    args = parser.parse_args()

    runtime = FamilyDocumentRuntime(
        source=LocalDirectoryDocumentSource(Path(args.inbox)),
        archive_sink=_archive_sink(Path(args.archive), Path(args.private_memory_root)),
        outbox=FamilyDocumentReceiptOutbox(Path(args.outbox_db)),
    )
    print(json.dumps(runtime.scan_once(drain=not args.no_drain), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
