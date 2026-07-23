#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.family_document_intake import build_request, process_intake


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run synthetic family document intake.")
    parser.add_argument("request", type=Path)
    args = parser.parse_args(argv)
    payload = json.loads(args.request.read_text(encoding="utf-8"))
    receipt = process_intake(build_request(payload))
    print(json.dumps(receipt, sort_keys=True, indent=2))
    return 0 if receipt["status"] in {"DONE", "DRY_RUN", "PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
