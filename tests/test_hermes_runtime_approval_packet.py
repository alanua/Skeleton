from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET_PATH = ROOT / "docs" / "hermes_runtime_approval_packet.md"


def packet_text() -> str:
    return PACKET_PATH.read_text(encoding="utf-8")


def test_hermes_runtime_approval_packet_exists() -> None:
    assert PACKET_PATH.is_file()


def test_hermes_runtime_approval_packet_has_required_sections() -> None:
    text = packet_text()

    for section in [
        "# Hermes Runtime Approval Packet Template",
        "## Exact Scope",
        "## Allowed Files",
        "## Forbidden Actions",
        "## Validation",
        "## Rollback",
        "## Evidence",
        "## Privacy Boundary",
        "## Stop Conditions",
        "## Approval Record",
        "## Template Result",
    ]:
        assert section in text


def test_packet_declares_planning_only_no_authority_boundary() -> None:
    text = packet_text()
    normalized = " ".join(text.split())

    for expected in [
        "planning-only public-safe template",
        "The packet itself grants no execution authority",
        "Completing this packet does not approve work",
        "Passing validation does not approve work",
        "Hermes is still not a runtime service",
        "No execution authority is granted by this packet template",
    ]:
        assert expected in text

    assert (
        "Silence, prior approval, a Hermes recommendation, a dry-run result, "
        "or an existing readiness audit is not operator approval"
    ) in normalized


def test_packet_requires_operator_approval_fields_for_each_control_area() -> None:
    text = packet_text()

    assert text.count("Required operator approval fields:") >= 8
    for expected in [
        "Operator approving this exact scope:",
        "Operator approving the allowed file list:",
        "Forbidden-action checklist reviewed by:",
        "Operator approving validation:",
        "Operator approving rollback:",
        "Operator approving evidence capture:",
        "Operator approving the privacy boundary:",
        "Exact approval statement:",
        "Exact scope approved:",
        "Exact files approved:",
        "Exact stop conditions accepted:",
    ]:
        assert expected in text


def test_packet_lists_critical_forbidden_actions() -> None:
    text = packet_text()

    for expected in [
        "install work",
        "service work",
        "network work",
        "workflow changes",
        "Runner loop changes",
        "runtime changes",
        "server changes",
        "queue mutation",
        "issue mutation",
        "branch publishing, pushing, or pull request creation",
        "merge, deploy, release, or canon promotion",
        "private data access",
        "secret access",
    ]:
        assert expected in text


def test_packet_defines_validation_rollback_evidence_and_privacy_boundaries() -> None:
    text = packet_text()

    for expected in [
        "Validation proves only the stated checks",
        "Validation never grants execution authority",
        "The proposal must define rollback before any durable action is approved",
        "If rollback cannot be expressed in public-safe terms",
        "Evidence must be sufficient for a reviewer to reproduce",
        "If evidence cannot be made public-safe, the proposal must stop",
        "The packet and all evidence must be public-safe",
        "secrets, credentials, tokens, keys, cookies, or session material",
        "raw private data",
        "private URLs or private file paths",
    ]:
        assert expected in text


def test_packet_contains_explicit_stop_language() -> None:
    text = packet_text()
    normalized = " ".join(text.split())

    for expected in [
        "exact operator approval is missing",
        "any required operator approval field is blank",
        "scope is ambiguous",
        "allowed files are missing",
        "a requested action appears in forbidden actions without explicit approval",
        "validation is missing",
        "rollback is missing",
        "evidence is missing",
        "Any privacy-boundary failure blocks the work",
        "Stop means stop",
    ]:
        assert expected in text

    assert (
        "Do not infer approval from context, tests, prior discussions, Hermes "
        "output, dry-run output, or absence of objections"
    ) in normalized
