#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.hetzner_control_mcp import handle_jsonrpc_message


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        response = handle_jsonrpc_message(json.loads(line))
        if response is not None:
            print(json.dumps(response, sort_keys=True, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
