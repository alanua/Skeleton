from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_local_operations_status_is_review_only_and_local() -> None:
    status = yaml.safe_load((ROOT / "docs" / "SKELETON_LOCAL_MEMORY_AND_AUFMASS_STATUS.yaml").read_text(encoding="utf-8"))
    assert status["status"] == "REVIEW"
    assert status["runtime"] == {
        "public_port": False,
        "system_service": False,
        "deployment": False,
    }
    assert status["privacy"]["private_values_in_github"] is False
    assert status["privacy"]["local_private_root_required"] is True
