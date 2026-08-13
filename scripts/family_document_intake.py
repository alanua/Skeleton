#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.family_document_intake import FamilyDocumentIntake, FamilyDocumentIntakeConfig
from core.memory_gateway import MemoryGateway, capability_token
from core.memory_gateway_storage import PrivateMemoryGatewayStorage
from core.private_memory_stack import PrivateMemoryStack


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one local family document intake pass.")
    parser.add_argument("config", type=Path)
    parser.add_argument("--dry-run-reconcile", action="store_true")
    args = parser.parse_args()
    config = FamilyDocumentIntakeConfig.from_mapping(json.loads(args.config.read_text(encoding="utf-8")))
    stack = PrivateMemoryStack(config.runtime_root / "synthetic_private_memory")
    stack.init(import_manifest=False)
    gateway = MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=False),
        private_memory_storage=PrivateMemoryGatewayStorage(stack),
    )
    intake = FamilyDocumentIntake(config, gateway)
    receipt = intake.reconcile_dry_run() if args.dry_run_reconcile else intake.process_one()
    print(json.dumps(receipt or {"status": "IDLE", "privacy": "aggregate_only"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
