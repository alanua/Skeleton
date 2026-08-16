#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from core.family_document_local_inference import load_exact_subject_aliases
from core.family_document_runtime import (
    FamilyDocumentReceiptOutbox,
    FamilyDocumentRuntime,
    QueuedLocalInferenceClassifier,
)
from core.family_document_sinks import (
    CompositeFamilyDocumentArchive,
    FileFamilyDocumentArchive,
    MemoryGatewayFamilyDocumentArchive,
)
from core.family_document_sources import LocalDirectoryDocumentSource
from core.memory_gateway import MemoryGateway, capability_token
from core.memory_gateway_storage import PrivateMemoryGatewayStorage
from core.private_memory_stack import PrivateMemoryStack


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name.lower()}_missing")
    return value


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


def _classifier(*, wait_timeout_seconds: float) -> QueuedLocalInferenceClassifier:
    aliases = load_exact_subject_aliases(_required_env("SKELETON_FAMILY_SUBJECT_ALIASES_FILE"))
    return QueuedLocalInferenceClassifier(
        Path(_required_env("SKELETON_LOCAL_INFERENCE_ROOT")),
        allowed_subject_aliases=aliases,
        model=_required_env("SKELETON_LOCAL_INFERENCE_DEFAULT_MODEL"),
        wait_timeout_seconds=wait_timeout_seconds,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the family-document intake worker.")
    parser.add_argument("--inbox", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--outbox-db", required=True)
    parser.add_argument("--private-memory-root", required=True)
    parser.add_argument("--classification-wait-seconds", type=float, default=180.0)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    runtime = FamilyDocumentRuntime(
        source=LocalDirectoryDocumentSource(Path(args.inbox)),
        archive_sink=_archive_sink(Path(args.archive), Path(args.private_memory_root)),
        outbox=FamilyDocumentReceiptOutbox(Path(args.outbox_db)),
        classifier=_classifier(wait_timeout_seconds=args.classification_wait_seconds),
    )
    while True:
        print(json.dumps(runtime.scan_once(), sort_keys=True), flush=True)
        if args.once:
            return 0
        time.sleep(max(args.interval_seconds, 1.0))


if __name__ == "__main__":
    raise SystemExit(main())
