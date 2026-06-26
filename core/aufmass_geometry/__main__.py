from __future__ import annotations

import argparse
import json
import sys

from core.aufmass_geometry.facade import build_capability_report, process_geometry_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aufmass core2d geometry JSON CLI")
    parser.add_argument("input", nargs="?", help="JSON input file. Reads stdin when omitted.")
    parser.add_argument("--capabilities", action="store_true", help="Emit capability report.")
    args = parser.parse_args(argv)

    if args.capabilities:
        payload = build_capability_report()
    elif args.input:
        payload = process_geometry_file(args.input)
    else:
        from core.aufmass_geometry.facade import process_geometry

        payload = process_geometry(json.load(sys.stdin))
    json.dump(payload, sys.stdout, sort_keys=True, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
