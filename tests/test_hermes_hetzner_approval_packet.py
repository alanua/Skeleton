from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET_PATH = ROOT / "docs" / "hermes_hetzner_approval_packet.md"


def packet_text() -> str:
    return PACKET_PATH.read_text(encoding="utf-8")


def normalized_text() -> str:
    return " ".join(packet_text().split())


def test_hermes_hetzner_approval_packet_exists() -> None:
    assert PACKET_PATH.is_file()


def test_hermes_hetzner_packet_has_required_sections() -> None:
    text = packet_text()

    for section in [
        "# Hermes Hetzner Approval Packet Template",
        "## Scope",
        "## Allowed Files",
        "## Forbidden Actions",
        "## Validation",
        "## Rollback",
        "## Evidence",
        "## Privacy Boundary",
        "## Operator Approval",
        "## Stop Conditions",
        "## Safe First Server-Readiness Checks",
        "## Template Result",
    ]:
        assert section in text


def test_packet_separates_phone_hermes_from_hetzner_hermes() -> None:
    text = packet_text()
    normalized = normalized_text()

    for expected in [
        "The phone Termux Hermes agent and the future Hetzner Hermes instance are separate systems",
        "The phone Hermes agent must not be reused, copied, migrated, or treated as approval",
        "The Hetzner Hermes instance must be planned as its own controlled server-side component",
        "Confirmation that phone Hermes is out of scope:",
        "Confirmation that Hetzner Hermes is a separate controlled component:",
        "phone Hermes reuse, migration, or coupling",
        "No phone Hermes reuse is granted by this packet template",
    ]:
        assert expected in normalized or expected in text


def test_packet_declares_no_authority_and_no_implicit_approval() -> None:
    text = packet_text()
    normalized = normalized_text()

    for expected in [
        "planning-only public-safe template",
        "The document itself grants no authority to install or run Hermes",
        "Completing this packet does not approve work",
        "Passing validation does not approve work",
        "No server install, service setup, network access, Telegram bridge, Runner bridge, background process, or secret use is approved by this template",
        "No execution authority is granted by this packet template",
        "No install authority is granted by this packet template",
        "No service authority is granted by this packet template",
        "No network authority is granted by this packet template",
        "No Telegram bridge authority is granted by this packet template",
        "No Runner bridge authority is granted by this packet template",
        "No background process authority is granted by this packet template",
        "No secret-use authority is granted by this packet template",
    ]:
        assert expected in normalized or expected in text

    assert (
        "Silence, prior approval, phone Hermes state, a Hermes recommendation, "
        "a dry-run result, readiness notes, validation output, or absence of "
        "objections is not operator approval"
    ) in normalized


def test_packet_requires_approval_before_critical_hetzner_actions() -> None:
    text = packet_text()
    normalized = normalized_text()

    assert text.count("Required operator approval fields:") >= 8

    for expected in [
        "server install",
        "service setup",
        "network access",
        "Telegram bridge",
        "Runner bridge",
        "background process",
        "secret use",
        "Operator approving this exact scope:",
        "Operator approving the allowed file list:",
        "Operator approving validation:",
        "Operator approving rollback:",
        "Operator approving evidence capture:",
        "Operator approving the privacy boundary:",
        "Exact approval statement:",
        "Exact stop conditions accepted:",
    ]:
        assert expected in normalized or expected in text


def test_packet_lists_forbidden_actions_and_blocks_install_service_network_work() -> None:
    text = packet_text()
    normalized = normalized_text()

    for expected in [
        "install work",
        "package manager work",
        "service work",
        "systemd, timer, daemon, queue consumer, or background process work",
        "server mutation",
        "network access",
        "network change",
        "firewall change",
        "SSH setup or remote shell use",
        "Telegram bridge implementation",
        "Runner bridge implementation",
        "workflow changes",
        "runtime changes",
        "private data access",
        "secret access",
        "credential, token, key, cookie, or session material access",
        "If an action is not explicitly approved, it is forbidden",
    ]:
        assert expected in normalized or expected in text


def test_packet_defines_validation_rollback_evidence_privacy_and_readiness_boundaries() -> None:
    text = packet_text()
    normalized = normalized_text()

    for expected in [
        "Validation proves only the stated checks",
        "Validation never grants install authority",
        "The proposal must define rollback before any durable action is approved",
        "If rollback cannot be expressed in public-safe terms",
        "Evidence must be sufficient for a reviewer to reproduce",
        "If evidence cannot be made public-safe, the proposal must stop",
        "The packet and all evidence must be public-safe",
        "Any privacy-boundary failure blocks the work",
        "These checks are planning gates only",
        "They do not authorize connecting to, installing on, mutating, or running anything on a server",
        "Before any live server check is proposed, a separate reviewed approval packet must define",
    ]:
        assert expected in normalized or expected in text


def test_packet_contains_no_secrets_no_private_data_no_real_identifier_language() -> None:
    text = packet_text()
    normalized = normalized_text()

    for expected in [
        "Do not include server IPs, hostnames, user names, secrets, private paths, tokens, API keys, or private runtime data",
        "server IPs, hostnames, user names, or private host paths",
        "secrets, credentials, tokens, keys, cookies, API keys, or session material",
        "raw private data",
        "private URLs or private file paths",
        "private runtime data from phone Hermes or any future Hetzner Hermes process",
        "Real server identifiers excluded",
    ]:
        assert expected in normalized or expected in text


def test_packet_does_not_contain_obvious_real_server_identifiers_or_secrets() -> None:
    text = packet_text()

    assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
    assert "BEGIN OPENSSH PRIVATE KEY" not in text
    assert "BEGIN RSA PRIVATE KEY" not in text
    assert "api_key=" not in text.lower()
    assert "token=" not in text.lower()
    assert "password=" not in text.lower()


def test_packet_contains_explicit_stop_language() -> None:
    text = packet_text()
    normalized = normalized_text()

    for expected in [
        "exact operator approval is missing",
        "any required operator approval field is blank",
        "scope is ambiguous",
        "phone Hermes would be reused, copied, migrated, coupled, or treated as the Hetzner Hermes instance",
        "allowed files are missing",
        "a requested action appears in forbidden actions without explicit approval",
        "validation is missing",
        "rollback is missing",
        "evidence is missing",
        "Stop means stop",
    ]:
        assert expected in normalized or expected in text

    assert (
        "Do not infer approval from context, tests, prior discussions, phone "
        "Hermes behavior, Hetzner readiness notes, dry-run output, or absence "
        "of objections"
    ) in normalized
