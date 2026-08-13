#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.family_document_intake import FamilyDocumentIntake, FamilyDocumentIntakeConfig
from core.family_document_runtime import FamilyDocumentWorker
from core.memory_gateway import MemoryGateway, capability_token
from core.memory_gateway_storage import PrivateMemoryGatewayStorage
from core.private_memory_stack import PrivateMemoryStack


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the single-instance family document intake worker.")
    parser.add_argument("config", type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args()
    config = FamilyDocumentIntakeConfig.from_mapping(json.loads(args.config.read_text(encoding="utf-8")))
    stack = PrivateMemoryStack(config.runtime_root / "synthetic_private_memory")
    stack.init(import_manifest=False)
    gateway = MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=False),
        private_memory_storage=PrivateMemoryGatewayStorage(stack),
    )
    worker = FamilyDocumentWorker(
        config,
        FamilyDocumentIntake(config, gateway),
        max_attempts=config.max_attempts,
        backoff_seconds=config.backoff_seconds,
    )
    if args.once:
        print(json.dumps(worker.run_once(), sort_keys=True))
    else:
        worker.run_forever(poll_seconds=args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
