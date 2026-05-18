from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def test_project_index_entries_have_manifest_and_state_files() -> None:
    index = load_yaml("PROJECT_INDEX.yaml")

    for project_id, spec in index["projects"].items():
        entrypoint = spec["entrypoint"]
        manifest_path = ROOT / entrypoint
        assert manifest_path.is_file(), project_id

        manifest = load_yaml(entrypoint)
        assert manifest["project_id"] == project_id

        for rel in manifest["read_order"]:
            assert (ROOT / rel).is_file(), f"{project_id}: {rel}"


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
