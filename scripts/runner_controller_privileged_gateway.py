#!/usr/bin/python3 -I
from __future__ import annotations

import sys
from pathlib import Path

INSTALL_ROOT = Path("/usr/local/lib/skeleton/runner-controller")
sys.path[:] = [str(INSTALL_ROOT), *sys.path]

from core.runner_controller_privileged_gateway import MAX_REQUEST_BYTES, execute_stdin


def main() -> int:
    code, output = execute_stdin(sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1))
    sys.stdout.buffer.write(output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
