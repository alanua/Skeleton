#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import pwd
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.home_edge.media_source_snapshot import OPERATOR_APPROVAL, build_snapshot_request


CANONICAL_RUNNER_SERVICE_USER = "agent"
CANONICAL_RUNNER_SERVICE = "skeleton-runner-poll.service"
FIXED_INSTALLED_COMMAND = (
    "/usr/bin/python3",
    "/usr/local/lib/skeleton-home-edge-controller/scripts/home_edge_media_source_snapshot_signer.py",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sign the fixed Home Edge media source snapshot request."
    )
    parser.add_argument("--operator-approval", required=True)
    parser.add_argument(
        "--print-fixed-command",
        action="store_true",
        help="Print the canonical installed command and exit without signing.",
    )
    args = parser.parse_args(argv)

    if args.print_fixed_command:
        print(json.dumps(list(FIXED_INSTALLED_COMMAND), sort_keys=True))
        return 0
    if args.operator_approval != OPERATOR_APPROVAL:
        raise SystemExit("operator_approval_mismatch")
    _require_canonical_runner_identity()
    print(json.dumps(build_snapshot_request(environment=os.environ).to_mapping(), sort_keys=True))
    return 0


def _require_canonical_runner_identity() -> None:
    try:
        user = pwd.getpwuid(os.geteuid()).pw_name
    except KeyError as exc:
        raise SystemExit("canonical_runner_identity_unavailable") from exc
    if user != CANONICAL_RUNNER_SERVICE_USER:
        raise SystemExit("canonical_runner_identity_mismatch")


if __name__ == "__main__":
    raise SystemExit(main())
