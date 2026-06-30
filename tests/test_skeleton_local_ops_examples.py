from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_documented_files_exist() -> None:
    assert (ROOT / "scripts" / "skeleton_local_ops.py").is_file()
    assert (ROOT / "scripts" / "install_skeleton_local_ops.sh").is_file()
    assert (ROOT / "schemas" / "aufmass_local_input.schema.json").is_file()
    assert (ROOT / "schemas" / "skeleton_local_memory_packet.schema.json").is_file()
    assert (ROOT / "docs" / "SKELETON_LOCAL_MEMORY_AND_AUFMASS.md").is_file()
