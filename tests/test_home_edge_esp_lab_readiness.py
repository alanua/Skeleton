from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from core.home_edge import esp_lab_readiness


ROOT = Path(__file__).resolve().parents[1]


def test_readiness_report_is_compact_public_safe_and_default_no_flash() -> None:
    report = esp_lab_readiness.build_readiness_report(ROOT)

    assert report == {
        "schema": "skeleton.home_edge.esp_lab_readiness.v1",
        "repository": "alanua/Skeleton",
        "status": "READY",
        "stage_availability": {
            "read_only_lab": True,
            "windows_stage_b_connector": True,
        },
        "contracts": {
            "signer_install": True,
            "activation": True,
        },
        "read_only_operations": [
            "discover_serial_candidates",
            "identify_chip",
            "inspect_flash_identity",
            "observe_serial_bounded",
        ],
        "firmware_flash_allowed": False,
        "live_home_edge_action_allowed": False,
        "privileged_mutation_allowed": False,
        "blockers": [],
    }


def test_readiness_report_records_missing_public_contract_blockers(tmp_path: Path) -> None:
    shutil.copytree(ROOT / "core", tmp_path / "core")
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    shutil.copytree(ROOT / "schemas", tmp_path / "schemas")
    (tmp_path / "scripts/home_edge_esp_lab_activation_signer").unlink()
    (tmp_path / "schemas/home_edge_esp_lab_connector_receipt.schema.json").unlink()

    report = esp_lab_readiness.build_readiness_report(tmp_path)

    assert report["status"] == "BLOCKED"
    assert report["stage_availability"] == {
        "read_only_lab": True,
        "windows_stage_b_connector": False,
    }
    assert report["contracts"] == {
        "signer_install": False,
        "activation": False,
    }
    assert report["firmware_flash_allowed"] is False
    assert report["blockers"] == sorted(
        [
            "missing_activation_file:scripts/home_edge_esp_lab_activation_signer",
            "missing_signer_install_file:scripts/home_edge_esp_lab_activation_signer",
            "missing_stage_file:windows_stage_b_connector:schemas/home_edge_esp_lab_connector_receipt.schema.json",
        ]
    )


def test_readiness_cli_emits_single_line_machine_readable_json() -> None:
    completed = subprocess.run(
        [
            "/usr/bin/python3",
            "-m",
            "core.home_edge.esp_lab_readiness",
            "--repo-root",
            str(ROOT),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert completed.stdout.count("\n") == 1
    report = json.loads(completed.stdout)
    assert report["schema"] == "skeleton.home_edge.esp_lab_readiness.v1"
    assert report["firmware_flash_allowed"] is False
    assert report["blockers"] == []


def test_readiness_report_does_not_execute_live_or_privileged_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_run(*_: Any, **__: Any) -> None:
        raise AssertionError("readiness reporter must not execute subprocesses")

    monkeypatch.setattr(subprocess, "run", fail_run)

    report = esp_lab_readiness.build_readiness_report(ROOT)

    assert report["status"] == "READY"
    assert report["live_home_edge_action_allowed"] is False
    assert report["privileged_mutation_allowed"] is False
