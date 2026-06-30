from __future__ import annotations

import py_compile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_local_operations_script_compiles() -> None:
    py_compile.compile(str(ROOT / "scripts" / "skeleton_local_ops.py"), doraise=True)
