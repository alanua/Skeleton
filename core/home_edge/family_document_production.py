from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Mapping

from core.family_document_calendar import SchedulerFamilyDocumentCalendar
from core.family_document_local_inference import load_exact_subject_aliases
from core.family_document_runtime import FamilyDocumentReceiptOutbox, FamilyDocumentRuntime
from core.family_document_sinks import VerifiedMemoryGatewayFamilyDocumentArchive
from core.family_document_sources import LocalDirectoryDocumentSource
from core.family_document_taxonomy import classify_family_document_text
from core.local_document_ocr import assert_default_local_ocr_available
from core.memory_gateway import MemoryGateway, capability_token
from core.memory_gateway_storage import PrivateMemoryGatewayStorage
from core.private_memory_stack import PrivateMemoryStack
from core.scheduler_store import SchedulerStore


DEFAULT_SCHEDULER_DB = "/var/lib/skeleton/scheduler/scheduler.sqlite3"


def family_document_classifier_from_env() -> Callable[[str], Mapping[str, Any]] | None:
    aliases_file = os.environ.get("SKELETON_FAMILY_SUBJECT_ALIASES_FILE", "").strip()
    if not aliases_file:
        return None
    aliases = load_exact_subject_aliases(aliases_file)
    return lambda text: classify_family_document_text(text, aliases)


def canonical_family_document_gateway() -> MemoryGateway:
    stack = PrivateMemoryStack()
    if not stack.paths.db.is_file():
        raise SystemExit("canonical_private_memory_unavailable")
    return MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=False),
        private_memory_storage=PrivateMemoryGatewayStorage(stack),
    )


def canonical_family_document_calendar() -> SchedulerFamilyDocumentCalendar:
    path = Path(os.environ.get("SKELETON_SCHEDULER_DB", DEFAULT_SCHEDULER_DB)).expanduser()
    if not path.is_file():
        raise SystemExit("canonical_scheduler_unavailable")
    return SchedulerFamilyDocumentCalendar(SchedulerStore(path))


def build_family_document_runtime(
    *,
    inbox: str | Path,
    archive: str | Path,
    outbox_db: str | Path,
) -> FamilyDocumentRuntime:
    assert_default_local_ocr_available()
    return FamilyDocumentRuntime(
        source=LocalDirectoryDocumentSource(Path(inbox)),
        archive_sink=VerifiedMemoryGatewayFamilyDocumentArchive(
            Path(archive),
            canonical_family_document_gateway(),
        ),
        outbox=FamilyDocumentReceiptOutbox(Path(outbox_db)),
        classifier=family_document_classifier_from_env(),
        calendar=canonical_family_document_calendar(),
    )
