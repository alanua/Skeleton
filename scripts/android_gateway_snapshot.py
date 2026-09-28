from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.skeleton_android_gateway import AndroidLocalCollector, AndroidObservationStore, snapshot_from_observations


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    collector = AndroidLocalCollector(node_id=args.node_id, source=args.source)
    observations = collector.collect()
    snapshot = snapshot_from_observations(observations)
    encoded = json.dumps(snapshot, indent=2, sort_keys=True) + "\n"

    if args.sqlite:
        with AndroidObservationStore(args.sqlite) as store:
            for observation in observations:
                store.ingest(observation)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")

    sys.stdout.write(encoded)
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect a local-first Android Gateway v1 snapshot.")
    parser.add_argument("--output", type=Path, help="Optional path that receives the snapshot JSON.")
    parser.add_argument("--sqlite", type=Path, help="Optional local SQLite path for explicit observation persistence.")
    parser.add_argument("--node-id", default="local-android", help="Bounded local node identifier.")
    parser.add_argument("--source", default="termux.local", help="Bounded local observation source identifier.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
