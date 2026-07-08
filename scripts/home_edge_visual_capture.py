#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.home_edge.visual_capture import VisualCaptureError, process_one_visual_capture_job


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args:
        print(
            json.dumps(
                {"status": "blocked", "reason": "visual capture tick accepts no job-controlled arguments"},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    try:
        receipt = process_one_visual_capture_job()
    except (OSError, VisualCaptureError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
