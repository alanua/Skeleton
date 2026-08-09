#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import os
import sys

ROOT = Path(os.environ.get("SKELETON_HOME_EDGE_REPO_ROOT", "")).resolve() if os.environ.get("SKELETON_HOME_EDGE_REPO_ROOT") else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.home_edge.display_power_off import signer_main


if __name__ == "__main__":
    raise SystemExit(signer_main())
