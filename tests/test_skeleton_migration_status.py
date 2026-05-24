from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_STATUS = ROOT / "projects/skeleton/MIGRATION_STATUS.yaml"
MIGRATION_DOC = ROOT / "docs/SKELETON_V2_MIGRATION.md"

PR_367_FILES = {
    "docs/KNOWLEDGE_INTAKE.md",
    "projects/skeleton/REVIEW_QUEUE.yaml",
    "tests/test_knowledge_intake.py",
    "scripts/runner_poll_github_tasks.py",
}


def load_status() -> dict:
    return yaml.safe_load(MIGRATION_STATUS.read_text(encoding="utf-8"))


def load_doc() -> str:
    return MIGRATION_DOC.read_text(encoding="utf-8")


def source_references() -> set[str]:
    status = load_status()
    return {
        source
        for entry in status["status_entries"]
        for source in entry["source_of_truth"]
    }


def test_migration_status_yaml_parses_and_has_required_top_level_fields() -> None:
    status = load_status()

    assert status["schema"] == "skeleton.migration_status.v1"
    assert status["project_id"] == "skeleton"
    assert isinstance(status["status_entries"], list)
    assert status["status_entries"]


def test_migration_status_entries_have_required_fields_and_categories() -> None:
    status = load_status()
    required_fields = {
        "id",
        "category",
        "summary",
        "source_of_truth",
        "state",
        "risk",
        "next_action",
    }
    required_categories = {
        "migrated",
        "pending_review",
        "rejected",
        "private_only",
    }

    categories = {entry["category"] for entry in status["status_entries"]}

    assert required_categories <= categories
    for entry in status["status_entries"]:
        assert required_fields <= set(entry), entry
        assert entry["id"]
        assert entry["summary"]
        assert isinstance(entry["source_of_truth"], list)
        assert entry["source_of_truth"]
        assert entry["state"]
        assert entry["risk"]
        assert entry["next_action"]


def test_migration_status_source_references_exist() -> None:
    missing = [source for source in sorted(source_references()) if not (ROOT / source).exists()]

    assert not missing


def test_migration_doc_mentions_required_framing() -> None:
    doc = load_doc()
    doc_lower = doc.lower()

    assert "BOOT_MANIFEST.yaml" in doc
    assert "chat memory is not canon" in doc_lower
    assert "jeeves is a separate future assistant/runtime" in doc_lower
    assert "explicit operator approval" in doc_lower


def test_migration_status_pack_does_not_require_pr_367_files() -> None:
    doc = load_doc()

    assert source_references().isdisjoint(PR_367_FILES)

    for path in PR_367_FILES:
        assert path not in doc
