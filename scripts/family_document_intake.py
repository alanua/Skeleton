#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from core.family_document_calendar import SchedulerFamilyDocumentCalendar
from core.family_document_local_inference import load_exact_subject_aliases
from core.family_document_runtime import FamilyDocumentReceiptOutbox, FamilyDocumentRuntime
from core.family_document_sinks import VerifiedMemoryGatewayFamilyDocumentArchive
from core.family_document_sources import LocalDirectoryDocumentSource
from core.family_document_taxonomy import classify_family_document_text
from core.memory_gateway import MemoryGateway, capability_token
from core.memory_gateway_storage import PrivateMemoryGatewayStorage
from core.private_memory_stack import PrivateMemoryStack
from core.scheduler_store import SchedulerStore


_DEFAULT_SCHEDULER_DB = "/var/lib/skeleton/scheduler/scheduler.sqlite3"


def _classifier() -> Callable[[str], Mapping[str, Any]] | None:
    aliases_file = os.environ.get("SKELETON_FAMILY_SUBJECT_ALIASES_FILE", "").strip()
    if not aliases_file:
        return None
    aliases = load_exact_subject_aliases(aliases_file)
    return lambda text: classify_family_document_text(text, aliases)


def _gateway() -> MemoryGateway:
    stack = PrivateMemoryStack()
    if not stack.paths.db.is_file():
        raise SystemExit("canonical_private_memory_unavailable")
    return MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=False),
        private_memory_storage=PrivateMemoryGatewayStorage(stack),
    )


def _calendar() -> SchedulerFamilyDocumentCalendar:
    path = Path(os.environ.get("SKELETON_SCHEDULER_DB", _DEFAULT_SCHEDULER_DB)).expanduser()
    if not path.is_file():
        raise SystemExit("canonical_scheduler_unavailable")
    return SchedulerFamilyDocumentCalendar(SchedulerStore(path))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one local family-document intake scan.")
    parser.add_argument("--inbox", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--outbox-db", required=True)
    parser.add_argument("--no-drain", action="store_true")
    args = parser.parse_args()

    runtime = FamilyDocumentRuntime(
        source=LocalDirectoryDocumentSource(Path(args.inbox)),
        archive_sink=VerifiedMemoryGatewayFamilyDocumentArchive(Path(args.archive), _gateway()),
        outbox=FamilyDocumentReceiptOutbox(Path(args.outbox_db)),
        classifier=_classifier(),
        calendar=_calendar(),
    )
    print(json.dumps(runtime.scan_once(drain=not args.no_drain), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
