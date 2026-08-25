#!/usr/bin/env python3
from __future__ import annotations

import sys

from core.runner_controller_privileged_gateway import execute_stdin


def main() -> int:
    code, output = execute_stdin(sys.stdin.buffer.read())
    sys.stdout.buffer.write(output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
