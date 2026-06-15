from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET_PATH = ROOT / "docs" / "hermes_hetzner_readiness_check_packet.md"


def packet_text() -> str:
    return PACKET_PATH.read_text(encoding="utf-8")


def normalized_text() -> str:
    return " ".join(packet_text().split())


def test_readiness_check_packet_exists() -> None:
    assert PACKET_PATH.is_file()


def test_packet_has_required_sections() -> None:
    text = packet_text()

    for section in [
        "# Hermes Hetzner Readiness Check Planning Packet",
        "## Purpose",
        "## Proposed Read-Only Check Questions",
        "## Public-Safe Example Command Categories",
        "## Approval Before Any Command",
        "## Output Redaction Rules",
        "## Privacy Boundary",
        "## Stop Conditions",
        "## Non-Authority Statement",
        "## Template Result",
    ]:
        assert section in text


def test_packet_defines_required_readiness_check_categories() -> None:
    text = packet_text()
    normalized = normalized_text()

    for expected in [
        "operating system family and supported release status",
        "Python availability and version family",
        "disk capacity and free-space summary",
        "RAM and swap capacity summary",
        "service manager availability",
        "location and retention shape of relevant logs",
        "backup existence and restore-readiness summary",
        "current process inventory at a bounded summary level",
        "listening port summary without exposing addresses",
        "file and directory permission model at a bounded summary level",
        "rollback needs before any future install or service work",
    ]:
        assert expected in normalized or expected in text


def test_packet_requires_separate_operator_approval_before_server_commands() -> None:
    text = packet_text()
    normalized = normalized_text()

    for expected in [
        "Actual server commands require separate operator approval before they are run",
        "No command may be run against any server unless a later approval record confirms",
        "the exact operator approving the command",
        "the exact read-only command category approved",
        "the expected public-safe output shape",
        "the redaction rules that must be applied before any output is shared",
        "the stop conditions for that command",
        "Silence, prior approval, repository tests, this packet, readiness notes, phone Hermes state, or absence of objections is not operator approval",
    ]:
        assert expected in normalized or expected in text


def test_packet_states_no_authority_for_hetzner_or_hermes_work() -> None:
    text = packet_text()
    normalized = normalized_text()

    for expected in [
        "This packet itself grants no authority to connect to Hetzner",
        "install Hermes",
        "start services",
        "change firewall rules",
        "create a Telegram bridge",
        "create a Runner bridge",
        "run background processes",
        "mutate server state",
        "use secrets",
        "run SSH or any remote shell",
        "approve any future separate Hermes install",
    ]:
        assert expected in normalized or expected in text


def test_packet_separates_phone_hermes_from_future_hetzner_instance() -> None:
    text = packet_text()
    normalized = normalized_text()

    for expected in [
        "The phone Termux Hermes agent is separate",
        "must not be reused, copied, migrated, coupled to, or treated as the Hetzner Hermes instance",
        "The future Hetzner Hermes instance is not installed and is not running",
    ]:
        assert expected in normalized or expected in text


def test_packet_lists_public_safe_example_command_categories_without_runnable_commands() -> None:
    text = packet_text()
    normalized = normalized_text()

    for expected in [
        "OS metadata read",
        "Python version read",
        "Disk summary read",
        "Memory summary read",
        "Service manager read",
        "Log policy read",
        "Backup status read",
        "Process summary read",
        "Port summary read",
        "Permission summary read",
        "Rollback inventory read",
        "The examples below are categories only",
        "They are not runnable commands",
    ]:
        assert expected in normalized or expected in text


def test_packet_defines_explicit_output_redaction_rules() -> None:
    text = packet_text()
    normalized = normalized_text()

    for expected in [
        "replace IP addresses, hostnames, and server labels with generic placeholders",
        "remove account names and private group names",
        "remove private host paths and private URLs",
        "remove secrets, credentials, tokens, keys, cookies, session material, API keys, and authentication headers",
        "remove customer data, operator private data, mailbox content, transcripts, and raw private payloads",
        "remove raw logs unless a separate approval allows a bounded sanitized excerpt",
        "remove full process command lines and keep only bounded conflict categories",
        "remove remote peers, network endpoints, and provider-specific identifiers",
        "summarize disk, RAM, process, port, backup, and permission results as bounded public-safe aggregates",
        "mark any omitted private field as redacted instead of leaving it blank",
    ]:
        assert expected in normalized or expected in text


def test_packet_defines_privacy_boundary_and_forbidden_evidence() -> None:
    text = packet_text()
    normalized = normalized_text()

    for expected in [
        "This packet and any future evidence must be public-safe",
        "approved read-only command category names",
        "sanitized exit status",
        "aggregate capacity, version, count, and readiness summaries",
        "real server identifiers",
        "IP addresses, hostnames, server labels, account names, private group names, or private host paths",
        "secrets, credentials, tokens, keys, cookies, API keys, session material, or authentication headers",
        "private URLs, raw logs, full process command lines, remote peers, or network endpoint details",
        "live server output that has not been reviewed and redacted",
        "Any privacy-boundary failure blocks the check and any follow-on work",
    ]:
        assert expected in normalized or expected in text


def test_packet_stop_conditions_block_mutation_private_data_and_identification() -> None:
    text = packet_text()
    normalized = normalized_text()

    for expected in [
        "mutate server state",
        "install, upgrade, remove, or configure packages",
        "start, stop, restart, enable, disable, or inspect services in a way that changes state",
        "create, edit, delete, move, upload, download, or chmod files",
        "change users, groups, permissions, firewall rules, routes, DNS, ports, network policy, or provider settings",
        "create a Telegram bridge or Runner bridge",
        "create a background process, daemon, timer, queue consumer, or workflow",
        "use, print, request, or validate secrets",
        "reveal private data, raw logs, private paths, account names, hostnames, IP addresses, server labels, remote peers, tokens, keys, or API keys",
        "identify the server or its operator",
        "produce output that cannot be redacted into a public-safe summary",
        "Stop means stop",
    ]:
        assert expected in normalized or expected in text


def test_packet_contains_no_real_server_identifiers_paths_or_secret_assignments() -> None:
    text = packet_text()
    lowered = text.lower()

    assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
    assert "BEGIN OPENSSH PRIVATE KEY" not in text
    assert "BEGIN RSA PRIVATE KEY" not in text
    assert "api_key=" not in lowered
    assert "token=" not in lowered
    assert "password=" not in lowered
    assert not re.search(r"(?im)^\s*ssh\s+", text)
    assert "/home/" not in text
    assert "/root/" not in text
