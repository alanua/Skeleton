#!/usr/bin/python3
from __future__ import annotations

import sys
from pathlib import Path


INSTALL_ROOT = Path("/usr/local/lib/skeleton-home-edge-snapshot-signer")
if INSTALL_ROOT.is_dir():
    system_prefixes = (sys.base_prefix, sys.prefix, "/usr/lib", "/usr/local/lib/python")
    sys.path[:] = [str(INSTALL_ROOT)] + [item for item in sys.path if item and item.startswith(system_prefixes)]

from core.home_edge.media_source_snapshot import signer_main


if __name__ == "__main__":
    raise SystemExit(signer_main())
