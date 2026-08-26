#!/usr/bin/python3 -I
from __future__ import annotations

import sys
from pathlib import Path

INSTALL_ROOT = Path("/usr/local/lib/skeleton/runner-controller")
sys.path.insert(0, str(INSTALL_ROOT))

from core.runner_controller_privileged_gateway_hardening import MAX_REQUEST_BYTES, execute_stdin


def main() -> int:
    data = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    code, output = execute_stdin(data)
    sys.stdout.buffer.write(output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
