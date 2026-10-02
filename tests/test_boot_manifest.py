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


def test_boot_report_includes_bounded_capability_runtime_truth() -> None:
    from core.boot_loader import BootLoader

    report = BootLoader(ROOT).load()
    truth = report["capability_runtime_truth"]

    assert truth["schema"] == "skeleton.capability_runtime_truth.v1"
    assert truth["registry_authority"] == "CAPABILITY_REGISTRY.yaml"
    assert truth["read_model"] == "bounded_typed_evidence_only"
    assert truth["runtime_probe_performed"] is False
    assert truth["runtime_mutation_performed"] is False


def test_boot_report_mapping_methods_include_capability_runtime_truth() -> None:
    from core.boot_loader import BootLoader

    report = BootLoader(ROOT).load()

    assert "capability_runtime_truth" in report.keys()
    assert "capability_runtime_truth" in dict(report.items())
    assert report["capability_runtime_truth"] in report.values()


def test_boot_loader_accepts_optional_typed_runtime_evidence_boundary() -> None:
    from core.boot_loader import BootLoader
    from core.capability_runtime_truth import RuntimeCapabilityEvidence

    report = BootLoader(ROOT).load(
        runtime_evidence=[
            RuntimeCapabilityEvidence(
                capability_id="boot_loader",
                runtime_state="available",
                source="unit_test",
                evidence_ref="test_boot_manifest",
            )
        ]
    )
    truth = report["capability_runtime_truth"]
    boot_loader = next(
        item for item in truth["capabilities"] if item["capability_id"] == "boot_loader"
    )

    assert boot_loader["effective_state"] == "available"
    assert boot_loader["freshness"] == "fresh_verified_runtime_evidence"
    assert boot_loader["drift"] == "no_drift"


def test_boot_loader_accepts_deterministic_runtime_truth_clock() -> None:
    from core.boot_loader import BootLoader

    report = BootLoader(ROOT).load(
        runtime_truth_checked_at="2026-10-02T00:00:00Z",
    )

    assert report["capability_runtime_truth"]["checked_at"] == "2026-10-02T00:00:00Z"
