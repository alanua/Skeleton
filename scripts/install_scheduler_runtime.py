#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.scheduler_runtime_install import (
    SchedulerRuntimeInstallError,
    failure_receipt,
    install_scheduler_runtime,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install the bounded user-level Skeleton Scheduler runtime."
    )
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--enable", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = install_scheduler_runtime(
            ROOT,
            expected_sha=args.expected_sha,
            enable=args.enable,
        )
        print(json.dumps(result.public_dict(), sort_keys=True))
        return 0
    except SchedulerRuntimeInstallError as exc:
        print(json.dumps(failure_receipt(args.expected_sha, exc.reason_code), sort_keys=True))
        return 2
    except Exception:
        print(
            json.dumps(
                failure_receipt(args.expected_sha, "SCHEDULER_RUNTIME_INSTALL_FAILED"),
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
