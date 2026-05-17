from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def test_boot_manifest_required_keys() -> None:
    manifest = load_yaml("BOOT_MANIFEST.yaml")

    for key in [
        "schema",
        "status",
        "repo",
        "ref",
        "entrypoint",
        "read_order",
        "boot_output",
        "failure_statuses",
    ]:
        assert key in manifest

    assert manifest["schema"] == "skeleton.boot_manifest.v1"
    assert manifest["repo"] == "alanua/Skeleton"
    assert manifest["entrypoint"] == "BOOT_MANIFEST.yaml"


def test_boot_manifest_read_order_files_exist() -> None:
    manifest = load_yaml("BOOT_MANIFEST.yaml")

    for rel in manifest["read_order"]:
        assert (ROOT / rel).is_file(), rel


def test_boot_manifest_default_read_order_excludes_reference_surfaces() -> None:
    manifest = load_yaml("BOOT_MANIFEST.yaml")
    forbidden_fragments = (
        "diary",
        "recovery",
        "history",
        "current_state",
        "CURRENT_STATE",
        "RUNBOOK",
        "CHATGPT_BRANCH_CONTINUITY_BOOT",
    )

    for rel in manifest["read_order"]:
        assert not any(fragment in rel for fragment in forbidden_fragments), rel


def test_boot_output_required_fields() -> None:
    manifest = load_yaml("BOOT_MANIFEST.yaml")
    fields = set(manifest["boot_output"]["required_fields"])

    assert fields == {
        "repo",
        "ref",
        "entrypoint",
        "loaded_sources",
        "mode",
        "active_project_status",
        "source_trust_map",
        "writes",
    }
