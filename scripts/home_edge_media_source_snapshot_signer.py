#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pwd
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.home_edge.media_source_snapshot import build_snapshot_request


CANONICAL_RUNNER_USER = "agent"


def _caller_is_canonical_runner() -> bool:
    try:
        return pwd.getpwuid(os.geteuid()).pw_name == CANONICAL_RUNNER_USER
    except KeyError:
        return False


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args != ["--sign"]:
        print("BLOCKED: unsupported signer invocation", file=sys.stderr)
        return 2
    if not _caller_is_canonical_runner():
        print("BLOCKED: signer caller is not canonical Runner service identity", file=sys.stderr)
        return 2
    try:
        request = build_snapshot_request(environment={})
    except Exception:  # noqa: BLE001 - signer stderr must stay public-safe.
        print("BLOCKED: signer private controller credential unavailable", file=sys.stderr)
        return 2
    print(json.dumps(request.to_mapping(), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
