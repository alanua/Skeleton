from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

from core.boot_loader import BootLoader


ROOT = Path(__file__).parents[1]


def load_report() -> dict:
    return BootLoader(ROOT).load()


def test_boot_loader_returns_dict() -> None:
    assert isinstance(load_report(), dict)


def test_boot_report_schema_is_v1() -> None:
    assert load_report()["schema"] == "skeleton.boot_report.v1"


def test_boot_report_has_all_v1_required_fields() -> None:
    assert set(load_report()) == {
        "schema",
        "repo",
        "ref",
        "entrypoint",
        "loaded_sources",
        "mode",
        "active_project_status",
        "source_trust_map",
        "writes",
    }


def test_boot_report_mode_is_boot() -> None:
    assert load_report()["mode"] == "boot"


def test_boot_report_writes_is_none_string() -> None:
    assert load_report()["writes"] == "none"


def test_boot_report_active_project_status_correct() -> None:
    assert load_report()["active_project_status"] == "ACTIVE_PROJECT_WAITING"


def test_boot_report_loaded_sources_is_list() -> None:
    assert isinstance(load_report()["loaded_sources"], list)


def test_boot_report_loaded_sources_includes_boot_manifest() -> None:
    assert "BOOT_MANIFEST.yaml" in load_report()["loaded_sources"]


def test_boot_report_source_trust_map_is_dict() -> None:
    assert isinstance(load_report()["source_trust_map"], dict)


def test_boot_loader_detects_missing_entrypoint(tmp_path: Path) -> None:
    manifest = yaml.safe_load((ROOT / "BOOT_MANIFEST.yaml").read_text(encoding="utf-8"))
    manifest["entrypoint"] = "MISSING_ENTRYPOINT.yaml"
    manifest["read_order"] = ["MISSING_ENTRYPOINT.yaml"]
    (tmp_path / "BOOT_MANIFEST.yaml").write_text(
        yaml.safe_dump(manifest), encoding="utf-8"
    )

    report = BootLoader(tmp_path).load()

    assert report["entrypoint"] not in report["loaded_sources"]


def test_cli_module_is_runnable() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "core.boot_loader"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout)["schema"] == "skeleton.boot_report.v1"
    assert result.stderr == ""


def test_no_v2_fields_in_report() -> None:
    report = load_report()

    for field in (
        "schema_version",
        "writes_performed",
        "available_capabilities",
        "planned_capabilities",
        "routing_tiers",
        "timestamp",
    ):
        assert field not in report
