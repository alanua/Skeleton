from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_PREFIX = "external:"


def load_yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def is_valid_external_read_order_reference(reference: str) -> bool:
    if not reference.startswith(EXTERNAL_PREFIX):
        return False

    external_path = reference.removeprefix(EXTERNAL_PREFIX)
    if external_path.startswith("/") or any(ord(char) < 32 or ord(char) == 127 for char in external_path):
        return False

    parts = external_path.split("/")
    if len(parts) < 3:
        return False

    return all(part and part not in {".", ".."} for part in parts)


def assert_read_order_entry_exists(project_id: str, reference: str) -> None:
    if reference.startswith(EXTERNAL_PREFIX):
        assert is_valid_external_read_order_reference(reference), f"{project_id}: {reference}"
        return

    local_path = ROOT / reference
    assert not local_path.is_absolute() or local_path.is_relative_to(ROOT), f"{project_id}: {reference}"
    assert local_path.resolve().is_relative_to(ROOT), f"{project_id}: {reference}"
    assert local_path.is_file(), f"{project_id}: {reference}"


def test_project_index_entries_have_manifest_and_state_files() -> None:
    index = load_yaml("PROJECT_INDEX.yaml")

    for project_id, spec in index["projects"].items():
        entrypoint = spec["entrypoint"]
        manifest_path = ROOT / entrypoint
        assert manifest_path.is_file(), project_id

        manifest = load_yaml(entrypoint)
        assert manifest["project_id"] == project_id

        for rel in manifest["read_order"]:
            assert_read_order_entry_exists(project_id, rel)


def test_external_read_order_reference_is_valid_without_local_checkout() -> None:
    assert is_valid_external_read_order_reference("external:owner/repo/path/to/file.yaml")


def test_external_read_order_reference_rejects_malformed_or_traversal_paths() -> None:
    invalid_references = [
        "owner/repo/path/to/file.yaml",
        "external:",
        "external:owner",
        "external:owner/repo",
        "external:/owner/repo/path.yaml",
        "external:owner//path.yaml",
        "external:owner/repo/",
        "external:owner/./path.yaml",
        "external:owner/repo/../path.yaml",
        "external:owner/repo/path\x1f.yaml",
        "external:owner/repo/path\x7f.yaml",
    ]

    for reference in invalid_references:
        assert not is_valid_external_read_order_reference(reference), reference


def test_project_manifests_are_self_entrypoints() -> None:
    index = load_yaml("PROJECT_INDEX.yaml")

    for project_id, spec in index["projects"].items():
        manifest = load_yaml(spec["entrypoint"])
        assert manifest["entrypoint"] == spec["entrypoint"], project_id
        assert manifest["read_order"][0] == spec["entrypoint"], project_id


def test_project_state_is_handoff_not_canon_truth() -> None:
    index = load_yaml("PROJECT_INDEX.yaml")

    for project_id, spec in index["projects"].items():
        manifest = load_yaml(spec["entrypoint"])
        state_path = manifest["read_order"][1]
        state = load_yaml(state_path)

        assert state["schema"] == "skeleton.project_state.v1"
        assert state["project_id"] == project_id
        assert state["state_role"] == "handoff_not_canon_truth"
        assert state["last_verified"]
        assert state["evidence_source"]
        assert state["summary"]
        assert state["next_actions"]


def test_private_sensitive_projects_are_marked() -> None:
    gewerbe = load_yaml("projects/gewerbe/PROJECT_MANIFEST.yaml")

    assert gewerbe["privacy_class"] == "private_sensitive"


def test_jeeves_is_separate_from_skeleton_core() -> None:
    skeleton = load_yaml("projects/skeleton/PROJECT_MANIFEST.yaml")
    jeeves = load_yaml("projects/jeeves/PROJECT_MANIFEST.yaml")

    assert skeleton["source_repo"] == "alanua/Skeleton"
    assert jeeves["source_repo"] == "alanua/jeeves"
    assert "separate" in jeeves["means"].lower()
