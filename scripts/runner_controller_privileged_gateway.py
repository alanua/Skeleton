#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

INSTALL_ROOTS = (
    Path("/usr/local/lib/skeleton/runner-controller"),
    Path(__file__).resolve().parents[3] / "lib/skeleton/runner-controller",
)
for install_root in INSTALL_ROOTS:
    if install_root.is_dir():
        sys.path.insert(0, str(install_root))
        break

from core.runner_controller_privileged_gateway_hardening import execute_stdin


def main() -> int:
    code, output = execute_stdin(sys.stdin.buffer.read())
    sys.stdout.buffer.write(output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
