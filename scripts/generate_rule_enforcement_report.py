from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.rule_enforcement_registry import build_report, validate_registry  # noqa: E402


DEFAULT_OUTPUT = ROOT / "docs" / "RULE_ENFORCEMENT_MATRIX.md"


def write_report(output: Path = DEFAULT_OUTPUT) -> Path:
    report = build_report(root=ROOT)
    output.write_text(report, encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the Skeleton rule enforcement matrix.")
    parser.add_argument("--check", action="store_true", help="fail if the generated report is not up to date")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    result = validate_registry(root=ROOT)
    if not result.ok:
        for error in result.errors:
            print(error, file=sys.stderr)
        return 1

    report = build_report(root=ROOT)
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current != report:
            print(f"{args.output} is not up to date", file=sys.stderr)
            return 1
        return 0

    args.output.write_text(report, encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
