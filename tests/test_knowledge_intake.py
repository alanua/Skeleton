from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
QUEUE_PATH = "projects/skeleton/REVIEW_QUEUE.yaml"
DOC_PATH = "docs/KNOWLEDGE_INTAKE.md"
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
ALLOWED_STATUS = {"REVIEW", "BACKLOG", "REJECTED"}
ALLOWED_CANON_STATUS = {"not_canon_until_promoted", "rejected_not_canon"}


def load_yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def review_queue_entries() -> list[dict]:
    return load_yaml(QUEUE_PATH)["entries"]


def queue_text() -> str:
    return "\n".join(
        " ".join(str(value) for value in entry.values())
        for entry in review_queue_entries()
    ).lower()


def entry_text(entry: dict) -> str:
    return " ".join(str(value) for value in entry.values()).lower()


def entries_matching(*phrases: str) -> list[dict]:
    return [
        entry
        for entry in review_queue_entries()
        if all(phrase.lower() in entry_text(entry) for phrase in phrases)
    ]


def require_entry(*phrases: str) -> dict:
    matches = entries_matching(*phrases)
    assert matches, f"missing queue entry containing: {phrases}"
    return matches[0]


def test_review_queue_parses_and_has_schema() -> None:
    queue = load_yaml(QUEUE_PATH)

    assert queue["schema"] == "skeleton.review_queue.v1"
    assert queue["project_id"] == "skeleton"
    assert queue["queue_role"] == "public_safe_knowledge_intake_not_canon"
    assert queue["created"] == "2026-05-24"
    assert isinstance(queue["entries"], list)
    assert queue["entries"]


def test_review_queue_entries_have_required_fields_and_statuses() -> None:
    ids = set()

    for entry in review_queue_entries():
        missing = REQUIRED_ENTRY_FIELDS - set(entry)
        assert not missing, f"{entry.get('id', '<missing id>')}: {sorted(missing)}"
        assert entry["id"] not in ids
        ids.add(entry["id"])
        assert entry["status"] in ALLOWED_STATUS
        assert entry["canon_status"] in ALLOWED_CANON_STATUS
        assert entry["existing_match"]
        assert entry["risk"]
        assert entry["recommended_action"]


def test_review_queue_entries_are_not_canon_until_promoted_unless_rejected() -> None:
    for entry in review_queue_entries():
        if entry["status"] == "REJECTED" or entry["classification"] == "REJECTED":
            assert entry["canon_status"] == "rejected_not_canon"
        else:
            assert entry["canon_status"] == "not_canon_until_promoted"


def test_review_queue_has_rejected_unsafe_patterns_entries() -> None:
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
        "uncontrolled autonomy",
        "secrets in chat/github/plain drive",
        "unsafe direct control of locks",
        "heaters",
        "230v",
        "security systems",
        "purchases",
        "cameras",
    ]:
        assert phrase in rejected_text


def test_review_queue_preserves_jeeves_boundary_entries() -> None:
    entry = require_entry("separate future assistant product and runtime", "not a skeleton adapter")

    assert entry["target_project"] == "jeeves"
    assert entry["source_batch"] == "recovered_jeeves_project_ideas"
    assert "skeleton/jeeves boundary" in entry_text(entry)
    assert entry["canon_status"] == "not_canon_until_promoted"


def test_review_queue_preserves_controlled_self_improvement_lifecycle() -> None:
    entry = require_entry("controlled self-improvement lifecycle")
    text = entry_text(entry)

    for phrase in [
        "observe",
        "detect",
        "propose",
        "validate",
        "approve",
        "apply",
        "monitor",
        "rollback",
        "operator approval remains mandatory",
    ]:
        assert phrase in text


def test_review_queue_preserves_no_autonomous_self_modification() -> None:
    entry = require_entry("no autonomous self-modification")
    text = entry_text(entry)

    for phrase in [
        "merge, deploy, secrets, runtime, execution-mode, and canon changes require explicit operator approval",
        "source/memory routing rules",
        "without human control",
    ]:
        assert phrase in text


def test_review_queue_preserves_multi_agent_role_split() -> None:
    entry = require_entry("multi-agent role split")
    text = entry_text(entry)

    for phrase in [
        "chatgpt as architect",
        "runner as bounded executor",
        "codex as implementation agent",
        "gemini and claude as auditors",
        "oleksii as final gate",
        "helper_registry",
        "approval gates",
    ]:
        assert phrase in text


def test_review_queue_preserves_memory_architecture_and_private_routing() -> None:
    entry = require_entry("memory architecture routes knowledge")
    text = entry_text(entry)

    for phrase in [
        "public github canon",
        "private memory",
        "archive/evidence",
        "temporary/noise",
        "secret manager",
        "memory_routing",
        "source_registry",
        "storing private context or secrets in public github would violate",
    ]:
        assert phrase in text


def test_review_queue_preserves_routing_and_telemetry_entries() -> None:
    routing = require_entry("llm routing ideas include local-first routing")
    telemetry = require_entry("telemetry and journaling for future jeeves")

    routing_text = entry_text(routing)
    for phrase in [
        "free and paid cloud fallback",
        "gemini audit tier",
        "fallback routing",
        "cost, latency, and quality telemetry",
        "provider_routing",
        "privacy and fallback rules",
    ]:
        assert phrase in routing_text

    telemetry_text = entry_text(telemetry)
    for phrase in [
        "prompt used",
        "agent selected",
        "model chosen",
        "cost",
        "latency",
        "success flag",
        "user correction",
        "fallback_used",
        "privacy-safe event schema",
    ]:
        assert phrase in telemetry_text


def test_review_queue_preserves_agent_department_concept() -> None:
    entry = require_entry("agent development department concept")
    text = entry_text(entry)

    for phrase in [
        "intake, planning, implementation, tests, review, reporting, and approval gates",
        "oleksii mostly approving or rejecting at defined gates",
        "merge, deploy, secrets, runtime, canon, or cross-repo writes",
        "bounded runner capability stages",
    ]:
        assert phrase in text


def test_review_queue_preserves_work_plan_control_entries() -> None:
    entry = require_entry("operator_work_plan_2026-05-24", "temporary control/backlog reference")
    text = entry_text(entry)

    assert entry["classification"] == "REVIEW/TEMPORARY_CONTROL"
    assert entry["status"] == "REVIEW"
    assert entry["canon_status"] == "not_canon_until_promoted"
    for phrase in [
        "operator_work_plan_2026-05-24",
        "bauclock stage 1 local-only",
        "audit_packet stage 1 after bauclock",
        "aufmass a1+a2",
        "temporary control/backlog reference",
        "reconcile against live issues and prs",
    ]:
        assert phrase in text


def test_review_queue_preserves_jeeves_product_vision_and_long_term_controls() -> None:
    product = require_entry("jeeves personal assistant vision")
    home = require_entry("home automation, tv, media, and music control")

    for phrase in ["durable memory", "tool use", "safety-first governance", "not active jeeves runtime"]:
        assert phrase in entry_text(product)

    for phrase in [
        "long-term jeeves runtime goal",
        "explicit permission",
        "private-environment handling",
        "skeleton may only plan or review controlled interfaces",
    ]:
        assert phrase in entry_text(home)


def test_knowledge_intake_doc_mentions_required_controls() -> None:
    doc = " ".join((ROOT / DOC_PATH).read_text(encoding="utf-8").lower().split())

    for phrase in [
        "bz",
        "review_queue",
        "public-safe",
        "not canon",
        "does not create live work",
        "does not activate runtime behavior",
        "explicit operator approval",
        "private data goes to `private_memory` only, not github",
        "secrets never belong in chat, github, or plain drive",
        "jeeves remains a separate future assistant product and runtime",
        "no agent or route may perform autonomous self-modification",
    ]:
        assert phrase in doc


def test_review_queue_records_public_safe_intake_without_runtime_activation() -> None:
    text = queue_text()

    for phrase in [
        "public-safe durable review",
        "not_canon_until_promoted",
        "not active jeeves runtime implementation",
        "not yet active runtime canon",
        "before runtime use",
    ]:
        assert phrase in text
