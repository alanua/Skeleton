#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.home_edge.diagnostics import DEFAULT_ARTIFACT_PATH, HomeEdgeDiagnosticError, build_operator_report
from core.home_edge.remote import AUDITED_COMMANDS, HomeEdgeRemoteError, run_audited_home_edge_command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run typed audited actions on the home-edge profile.")
    parser.add_argument("command", choices=sorted(AUDITED_COMMANDS))
    parser.add_argument("--artifact", default=str(DEFAULT_ARTIFACT_PATH))
    parser.add_argument("--operator-report", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = run_audited_home_edge_command(args.command, artifact_path=args.artifact)
    except (HomeEdgeDiagnosticError, HomeEdgeRemoteError, TimeoutError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2, sort_keys=True))
    if args.operator_report and args.command == "diagnostic":
        print()
        print(build_operator_report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
