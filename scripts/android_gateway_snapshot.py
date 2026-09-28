from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from json import JSONDecodeError
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.skeleton_android_gateway import SkeletonAndroidGateway


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    gateway = SkeletonAndroidGateway()
    for path in args.event:
        event = _load_json(path)
        gateway.try_ingest(event)
    print(json.dumps(gateway.snapshot(), indent=2, sort_keys=True))
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a public-safe provider-neutral Android telemetry gateway snapshot."
    )
    parser.add_argument(
        "--event",
        action="append",
        type=Path,
        default=[],
        help="Path to a JSON telemetry event. May be passed more than once.",
    )
    return parser.parse_args(argv)


def _load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, JSONDecodeError) as exc:
        raise SystemExit(f"failed to load event JSON: {path}") from exc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
