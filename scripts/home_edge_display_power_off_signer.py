#!/usr/bin/env python3
from __future__ import annotations

import json
import sys

from core.home_edge.display_power_off import signer_envelope_from_stdin


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        envelope = signer_envelope_from_stdin(sys.stdin.read(), argv=args)
    except Exception as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(envelope, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
