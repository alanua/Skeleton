from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "skeleton_local_ops.py"


def test_default_status_does_not_print_local_paths_or_values() -> None:
    text = CLI.read_text(encoding="utf-8")
    assert '"value" not in hidden' not in text
    assert "print(json.dumps(dispatch(args)" in text
    assert "error_class" in text
    assert "str(exc)" not in text


def test_private_data_stays_outside_repository() -> None:
    text = CLI.read_text(encoding="utf-8")
    assert "SKELETON_PRIVATE_ROOT" in text
    assert "canonical.sqlite" in text
    assert "--show-value" in text
    assert "systemctl" not in text
    assert "requests." not in text
