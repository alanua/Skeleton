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


def review_queue_entries() -> list[dict]:
    return load_yaml("projects/skeleton/REVIEW_QUEUE.yaml")["entries"]


def queue_text() -> str:
    return "\n".join(
        " ".join(str(value) for value in entry.values()) for entry in review_queue_entries()
    ).lower()


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
    rejected = [entry for entry in review_queue_entries() if entry["classification"] == "REJECTED"]

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


def test_review_queue_preserves_jeeves_boundary_entries() -> None:
    text = queue_text()

    for phrase in [
        "separate future assistant product and runtime",
        "not a skeleton adapter",
        "skeleton/jeeves boundary",
    ]:
        assert phrase in text


def test_review_queue_preserves_controlled_self_improvement_entry() -> None:
    text = queue_text()

    for phrase in [
        "controlled self-improvement lifecycle",
        "observe",
        "detect",
        "propose",
        "validate",
        "approve",
        "monitor",
        "rollback",
    ]:
        assert phrase in text


def test_review_queue_preserves_no_autonomous_self_modification_entry() -> None:
    text = queue_text()

    for phrase in [
        "no autonomous self-modification",
        "merge, deploy, secrets, runtime, execution-mode, and canon changes require explicit operator approval",
    ]:
        assert phrase in text


def test_review_queue_preserves_jeeves_agent_and_memory_architecture_entries() -> None:
    text = queue_text()

    for phrase in [
        "multi-agent role split",
        "chatgpt as architect",
        "runner as bounded executor",
        "codex as implementation agent",
        "gemini and claude as auditors",
        "memory architecture routes knowledge",
        "public github canon",
        "private memory",
        "secret manager",
    ]:
        assert phrase in text


def test_review_queue_preserves_jeeves_routing_and_telemetry_entries() -> None:
    text = queue_text()

    for phrase in [
        "llm routing ideas include local-first routing",
        "gemini audit tier",
        "cost, latency, and quality telemetry",
        "telemetry and journaling for future jeeves",
        "prompt used",
        "agent selected",
        "model chosen",
        "fallback_used",
    ]:
        assert phrase in text


def test_review_queue_preserves_agent_department_and_safety_rejections() -> None:
    text = queue_text()

    for phrase in [
        "agent development department concept",
        "intake, planning, implementation, tests, review, reporting, and approval gates",
        "uncontrolled autonomy",
        "unsafe direct control of locks",
        "230v",
        "security systems",
        "purchases",
        "cameras",
    ]:
        assert phrase in text


def test_review_queue_preserves_work_plan_control_entries() -> None:
    text = queue_text()

    for phrase in [
        "operator_work_plan_2026-05-24",
        "bauclock",
        "stage 1",
        "local",
        "only",
        "audit_packet",
        "after",
        "aufmass a1+a2",
        "temporary control/backlog reference",
        "reconcile against live issues and prs",
    ]:
        assert phrase in text


def test_review_queue_preserves_aufmass_and_sai_pipeline_entries() -> None:
    text = queue_text()

    for phrase in [
        "system first verifies whether calculation is possible",
        "evidence, confidence, and review queue",
        "not an agent calculating the whole building at once",
        "cheap executor to stronger verification to human",
        "machine output plus human report",
        "floors or apartments rather than the whole object at once",
    ]:
        assert phrase in text


def test_review_queue_preserves_sai_rejected_items() -> None:
    text = queue_text()

    for phrase in [
        "sai-derived rejected items",
        "uncontrolled revit/autocad automation",
        "agent controls everything",
        "normative answers without verification",
        "limit bypass or multi-account workarounds",
    ]:
        assert phrase in text


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
