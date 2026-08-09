#!/usr/bin/env python3
from __future__ import annotations

import json
import sys

from core.home_edge.display_power_off import build_display_power_off_request


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        print("display power-off signer accepts no operation arguments", file=sys.stderr)
        return 2
    request = build_display_power_off_request()
    print(json.dumps(request.to_mapping(), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
