#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.family_dns_policy import build_preflight_packet, public_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print the public-safe, read-only family DNS rollout preflight packet."
    )
    parser.add_argument("--request-prefix", default="family-dns-stage-a")
    args = parser.parse_args(argv)
    packet = build_preflight_packet(request_prefix=args.request_prefix)
    sys.stdout.write(public_json(packet))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
