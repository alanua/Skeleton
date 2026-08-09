#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.home_edge.media_source_snapshot import sign_snapshot_request_from_controller_stdin


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        envelope = sign_snapshot_request_from_controller_stdin(sys.stdin.read(), argv)
    except Exception as exc:  # noqa: BLE001 - public failure class only.
        reason = str(exc) if str(exc).startswith("controller_signer_") else "controller_signer_failed"
        print(json.dumps({"status": "blocked", "reason": reason}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(envelope, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
