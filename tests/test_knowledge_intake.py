from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ENTRY_FIELDS = {
    "id",
    "source_batch",
    "date",
    "classification",
    "target_project",
    "summary",
    "existing_match",
    "risk",
    "recommended_action",
    "status",
    "canon_status",
}


def load_yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def test_review_queue_parses_and_has_schema() -> None:
    queue = load_yaml("projects/skeleton/REVIEW_QUEUE.yaml")

    assert queue["schema"] == "skeleton.review_queue.v1"
    assert isinstance(queue["entries"], list)
    assert queue["entries"]


def test_review_queue_entries_have_required_fields() -> None:
    queue = load_yaml("projects/skeleton/REVIEW_QUEUE.yaml")

    for entry in queue["entries"]:
        missing = REQUIRED_ENTRY_FIELDS - set(entry)
        assert not missing, f"{entry.get('id', '<missing id>')}: {sorted(missing)}"


def test_review_queue_has_rejected_unsafe_patterns_entry() -> None:
    entries = load_yaml("projects/skeleton/REVIEW_QUEUE.yaml")["entries"]
    rejected = [entry for entry in entries if entry["classification"] == "REJECTED"]

    assert rejected
    rejected_text = "\n".join(
        f"{entry['summary']} {entry['risk']} {entry['recommended_action']}"
        for entry in rejected
    ).lower()
    for phrase in [
        "browser pat",
        "localstorage",
        "cors wildcard",
        "automatic git push",
        "blockchain/token",
    ]:
        assert phrase in rejected_text


def test_knowledge_intake_doc_mentions_required_controls() -> None:
    doc = (ROOT / "docs/KNOWLEDGE_INTAKE.md").read_text(encoding="utf-8").lower()

    for phrase in [
        "bz",
        "review_queue",
        "explicit operator approval",
        "private data",
        "secrets",
    ]:
        assert phrase in doc
