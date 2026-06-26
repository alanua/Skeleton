from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.aufmass_geometry import process_geometry_file


SYNTHETIC_FIXTURE = ROOT / "tests" / "fixtures" / "aufmass_synthetic" / "seitenfluegel_room.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Aufmass core2d deterministic benchmark")
    parser.add_argument("--synthetic", action="store_true", help="Run synthetic Seitenfluegel case.")
    args = parser.parse_args()
    if not args.synthetic:
        parser.error("--synthetic is required in v1")

    started = perf_counter()
    result = process_geometry_file(SYNTHETIC_FIXTURE)
    elapsed_ms = (perf_counter() - started) * 1000.0
    print(
        json.dumps(
            {
                "benchmark": "aufmass_geometry_core2d_synthetic",
                "fixture": "seitenfluegel_room",
                "status": result["status"],
                "ordered_wall_segment_count": len(result["ordered_wall_segments"]),
                "area_report_m2": result["accepted_room_shell"]["area_report_m2"],
                "perimeter_m": result["accepted_room_shell"]["perimeter_m"],
                "manifest_hash": result["manifest_hash"],
                "elapsed_ms": round(elapsed_ms, 3),
            },
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
