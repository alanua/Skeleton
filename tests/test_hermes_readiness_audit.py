from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / "docs" / "hermes_readiness_audit.md"


def audit_text() -> str:
    return AUDIT_PATH.read_text(encoding="utf-8")


def test_hermes_readiness_audit_exists() -> None:
    assert AUDIT_PATH.is_file()


def test_hermes_readiness_audit_has_required_sections() -> None:
    text = audit_text()

    for section in [
        "# Hermes Readiness Audit",
        "## Repository-Visible State",
        "## No-Secrets And No-Private-Data Boundary",
        "## Readiness Checks Before Future Install Or Runtime Work",
        "## Operator Approval Gates",
        "## Rollback Requirements",
        "## Evidence Requirements",
        "## Audit Result",
    ]:
        assert section in text


def test_hermes_readiness_audit_summarizes_only_visible_worker_v0_state() -> None:
    text = audit_text()

    for expected in [
        "summarizes only repository-visible Hermes Worker v0 state",
        "`docs/hermes_worker_v0.md`",
        "`schemas/hermes_task_packet.schema.json`",
        "`schemas/hermes_skill_manifest.schema.json`",
        "`core/hermes_worker.py`",
        "`fixtures/hermes_worker/`",
        "does not include an active Hermes service",
    ]:
        assert expected in text


def test_hermes_readiness_audit_blocks_install_runtime_and_network_work() -> None:
    text = audit_text()

    for expected in [
        "does not authorize install work",
        "service work",
        "workflow changes",
        "Runner loop changes",
        "runtime changes",
        "network work",
        "Failure of any readiness check blocks",
    ]:
        assert expected in text


def test_hermes_readiness_audit_requires_operator_gates_and_rollback() -> None:
    text = audit_text()
    normalized = " ".join(text.split())

    for expected in [
        "explicit operator approval gates",
        "Approval at one gate does not approve the next gate",
        "rollback plan",
        "validation commands that prove restoration",
        "evidence that must be retained after rollback",
    ]:
        assert expected in text
    assert (
        "Silence, passing tests, a Hermes recommendation, or a dry-run result "
        "is not operator approval"
    ) in normalized


def test_hermes_readiness_audit_requires_public_safe_evidence() -> None:
    text = audit_text()

    for expected in [
        "All readiness evidence for Hermes Worker v0 must be public-safe",
        "Forbidden evidence includes",
        "secrets, credentials, tokens, keys",
        "raw private data",
        "private URLs or private file paths",
        "No-Secrets And No-Private-Data Boundary",
        "Evidence must be sufficient for a reviewer to reproduce",
    ]:
        assert expected in text
