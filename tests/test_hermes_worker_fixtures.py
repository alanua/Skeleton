from __future__ import annotations

import json
from pathlib import Path

from core.hermes_worker import run_hermes_worker_dry_run


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "fixtures" / "hermes_worker"


def load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_review_only_fixture_returns_dry_run_ok() -> None:
    task_packet = load_fixture("task_packet_dry_run_ok.json")
    skill_manifest = load_fixture("skill_manifest_review_only.json")

    result = run_hermes_worker_dry_run(task_packet, skill_manifest)

    assert result["status"] == "DRY_RUN_OK"
    assert result["task_id"] == "SYNTH-HERMES-DRY-RUN-001"
    assert result["skill_id"] == "synthetic_review_only"
    assert result["mode"] == "dry_run"
    assert result["warnings"] == []
    assert result["diagnostics"]["missing_fields"] == []
    assert result["diagnostics"]["invalid_fields"] == []
    assert result["decision"] == {
        "allowed": True,
        "reason": "packet_satisfies_public_safe_dry_run_contract",
    }


def test_operator_approval_fixture_requires_operator_approval() -> None:
    task_packet = load_fixture("task_packet_dry_run_ok.json")
    skill_manifest = load_fixture("skill_manifest_operator_approval.json")

    result = run_hermes_worker_dry_run(task_packet, skill_manifest)

    assert result["status"] == "OPERATOR_APPROVAL_REQUIRED"
    assert result["skill_id"] == "synthetic_operator_gate"
    assert result["warnings"] == []
    assert result["diagnostics"]["missing_fields"] == []
    assert result["diagnostics"]["invalid_fields"] == []
    assert result["decision"] == {
        "allowed": False,
        "reason": "skill_tier_requires_operator_approval",
    }
