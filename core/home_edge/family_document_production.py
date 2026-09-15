from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from core.family_document_calendar import SchedulerFamilyDocumentCalendar
from core.family_document_local_inference import load_exact_subject_aliases
from core.family_document_runtime import FamilyDocumentReceiptOutbox, FamilyDocumentRuntime
from core.family_document_sinks import VerifiedMemoryGatewayFamilyDocumentArchive
from core.family_document_sources import LocalDirectoryDocumentSource
from core.family_document_taxonomy import classify_family_document_text
from core.local_document_ocr import LocalDocumentOcrError, require_local_ocr_dependencies
from core.memory_gateway import MemoryGateway, capability_token
from core.memory_gateway_storage import PrivateMemoryGatewayStorage
from core.private_memory_stack import PrivateMemoryStack
from core.scheduler_store import SchedulerStore


DEFAULT_SCHEDULER_DB = "/var/lib/skeleton/scheduler/scheduler.sqlite3"


@dataclass(frozen=True)
class FamilyDocumentProductionConfig:
    inbox: Path
    archive: Path
    outbox_db: Path
    scheduler_db: Path
    aliases_file: Path | None = None
    require_ocr_dependencies: bool = True


def config_from_args(
    *,
    inbox: str,
    archive: str,
    outbox_db: str,
    env: Mapping[str, str] | None = None,
    require_ocr_dependencies: bool = True,
) -> FamilyDocumentProductionConfig:
    environment = os.environ if env is None else env
    aliases = str(environment.get("SKELETON_FAMILY_SUBJECT_ALIASES_FILE", "")).strip()
    scheduler_db = str(environment.get("SKELETON_SCHEDULER_DB", DEFAULT_SCHEDULER_DB)).strip()
    return FamilyDocumentProductionConfig(
        inbox=Path(inbox).expanduser(),
        archive=Path(archive).expanduser(),
        outbox_db=Path(outbox_db).expanduser(),
        scheduler_db=Path(scheduler_db).expanduser(),
        aliases_file=Path(aliases).expanduser() if aliases else None,
        require_ocr_dependencies=require_ocr_dependencies,
    )


def build_family_document_production_runtime(config: FamilyDocumentProductionConfig) -> FamilyDocumentRuntime:
    source = LocalDirectoryDocumentSource(config.inbox)
    if config.require_ocr_dependencies:
        require_local_ocr_dependencies(suffixes=source.suffixes)
    return FamilyDocumentRuntime(
        source=source,
        archive_sink=VerifiedMemoryGatewayFamilyDocumentArchive(config.archive, _canonical_gateway()),
        outbox=FamilyDocumentReceiptOutbox(config.outbox_db),
        classifier=_classifier(config.aliases_file),
        calendar=_canonical_calendar(config.scheduler_db),
    )


def _classifier(aliases_file: Path | None) -> Callable[[str], Mapping[str, Any]] | None:
    if aliases_file is None:
        return None
    aliases = load_exact_subject_aliases(str(aliases_file))
    return lambda text: classify_family_document_text(text, aliases)


def _canonical_gateway() -> MemoryGateway:
    stack = PrivateMemoryStack()
    if not stack.paths.db.is_file():
        raise SystemExit("canonical_private_memory_unavailable")
    return MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=False),
        private_memory_storage=PrivateMemoryGatewayStorage(stack),
    )


def _canonical_calendar(path: Path) -> SchedulerFamilyDocumentCalendar:
    if not path.is_file():
        raise SystemExit("canonical_scheduler_unavailable")
    return SchedulerFamilyDocumentCalendar(SchedulerStore(path))
