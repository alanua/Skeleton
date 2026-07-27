#!/usr/bin/env python3
from __future__ import annotations

import argparse, json
from pathlib import Path

from scripts.family_document_worker import load_config
from core.family_document_intake import Intake


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    config, _ = load_config(args.config)
    receipt = Intake(config).process(args.source, dry_run=args.dry_run)
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt['status'] in {'DONE', 'REVIEW'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
