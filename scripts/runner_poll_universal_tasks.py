#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time

from scripts import runner_poll_github_tasks as legacy_runner


def poll_once(workdir: str | None = None) -> int:
    return legacy_runner.poll_once(workdir=workdir)


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll universal Runner tasks.")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--workdir", default=None)
    args = parser.parse_args()
    if args.loop:
        while True:
            poll_once(workdir=args.workdir)
            time.sleep(legacy_runner.POLL_INTERVAL)
    else:
        poll_once(workdir=args.workdir)


if __name__ == "__main__":
    main()
