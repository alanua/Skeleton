from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ARCH_MD = ROOT / "docs" / "SKELETON_ARCHITECTURE_VNEXT.md"
ARCH_YAML = ROOT / "docs" / "SKELETON_ARCHITECTURE_VNEXT.yaml"
BUILD_PLAN = ROOT / "docs" / "SKELETON_BUILD_PLAN.md"
BUILD_PLAN_YAML = ROOT / "docs" / "SKELETON_BUILD_PLAN.yaml"
ROADMAP = ROOT / "docs" / "DEVELOPMENT_DEPARTMENT_ROADMAP.md"

COMPONENTS = {
    "governance_kernel",
    "loop_controller",
    "task_envelope",
    "approval_object",
    "evidence_packet",
    "delivery_preflight",
    "private_sqlite_memory_gateway",
    "graphify",
    "mempalace",
    "home_edge_transport",
    "home_edge_mutations",
    "telegram_control_board",
}

FIRMWARE_CLASSES = {
    "green": "firmware build/validation",
    "yellow": "verified recoverable ESP/WLED OTA or serial flashing",
    "red": "modem/router/bootloader firmware, disk/boot/primary-gateway, or recovery-threatening action",
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_yaml() -> dict:
    return yaml.safe_load(read(ARCH_YAML))


def test_markdown_contains_required_status_sections_and_diagrams() -> None:
    text = read(ARCH_MD)

    assert "Status: ACTIVE_ARCHITECTURE / OPERATOR_APPROVED / HUMAN_GATED" in text
    assert "This document is repository canon only while present on reviewed GitHub `main`" in text
    assert "GitHub `main` remains public control/code/policy canon" in text
    assert "Canonical private SQLite remains the private source of truth" in text
    assert "`BOOT_MANIFEST.yaml` remains the entrypoint" in text
    assert "Until then" not in text
    assert "this task must not modify" not in text

    for section in [
        "## Identity And Boundary",
        "## Governance Kernel",
        "## Loop Controller",
        "## Canonical TaskEnvelope",
        "## Approval Object",
        "## Execution Broker And Worker Roles",
        "## Home Edge Node",
        "## Evidence And Delivery Plane",
        "## Memory And State Plane",
        "## Interface Projections",
        "## Architecture And Implementation Status Matrix",
        "## Supersession Map",
        "## Implementation Sequence",
        "## Required Decisions",
    ]:
        assert section in text

    assert "Operator\n  -> Governance Kernel\n  -> Loop Controller\n  -> Runner Execution Broker" in text
    assert "GitHub public canon + private SQLite canon\n  -> Memory Gateway" in text


def test_markdown_status_matrix_has_all_components_and_separate_statuses() -> None:
    text = read(ARCH_MD)
    heading = text.index("## Architecture And Implementation Status Matrix")
    section = text[heading:text.index("## Supersession Map")]

    assert "| Component | architecture_status | implementation_status |" in section
    assert "architecture_status" in section
    assert "implementation_status" in section
    for component in COMPONENTS:
        assert f"| `{component}` |" in section

    assert section.count("| `") == len(COMPONENTS)
    assert "planned controlled-mutation capability; no Home Edge mutation execution is live" in section
    assert "Graphify runtime, ingestion, Gateway activation, and private graph data access are not live" in section
    assert "MemPalace runtime, Gateway activation, and private semantic index access are not live" in section
    assert "not live or canonically activated" in section
    assert "not live or canonical activation" not in section


def test_yaml_has_status_matrix_with_exact_components_and_split_status_fields() -> None:
    matrix = load_yaml()["component_status_matrix"]

    assert set(matrix) == COMPONENTS
    assert len(matrix) == 12
    for component, entry in matrix.items():
        assert set(entry) == {"architecture_status", "implementation_status"}, component
        assert entry["architecture_status"]
        assert entry["implementation_status"]
        assert entry["architecture_status"] != entry["implementation_status"]


def test_home_edge_firmware_risk_classes_are_exact_in_markdown_and_yaml() -> None:
    text = read(ARCH_MD)
    classes = load_yaml()["home_edge_node"]["firmware_risk_classes"]

    assert classes == FIRMWARE_CLASSES
    assert "Firmware build/validation is green." in text
    assert "Verified recoverable ESP/WLED OTA or serial flashing is yellow." in text
    assert (
        "Modem/router/bootloader firmware, disk/boot/primary-gateway, or\n"
        "  recovery-threatening action is red."
    ) in text
    assert "all firmware changes require red" not in text.lower()


def test_public_unrestricted_shell_or_agent_api_is_prohibited() -> None:
    combined = read(ARCH_MD) + "\n" + read(ARCH_YAML)

    assert "Public unrestricted shell or agent API is prohibited." in read(ARCH_MD)
    assert "public_unrestricted_shell_or_agent_api_is_prohibited" in read(ARCH_YAML)
    assert "No public unrestricted shell or agent API" not in combined


def test_non_live_capabilities_are_not_falsely_marked_live() -> None:
    combined = read(ARCH_MD) + "\n" + read(ARCH_YAML)
    false_live_patterns = [
        r"Graphify (?:runtime|Gateway activation|private graph data access) (?:is|are) live",
        r"MemPalace (?:runtime|Gateway activation|private semantic index access) (?:is|are) live",
        r"(?<!no )Home Edge mutation execution is live",
        r"(?<!no )Home Edge transport is live",
        r"VNext Control Board [^\n]* is live",
        r"canonical activation is live",
        r"canonical import is live",
        r"#1253/#1254 (?:is|are) merged",
        r"#1256 [^\n]* is live execution",
    ]
    for pattern in false_live_patterns:
        assert not re.search(pattern, combined, flags=re.IGNORECASE)

    for phrase in ["must", "is prohibited", "once activated", "not live"]:
        assert phrase in combined


def test_yaml_contains_task_envelope_loop_approval_evidence_and_priorities() -> None:
    data = load_yaml()

    assert data["task_envelope"]["role"] == "canonical execution request"
    for field in [
        "task_id",
        "idempotency_key",
        "action",
        "executor_type",
        "allowed_files",
        "forbidden_actions",
        "privacy_boundary",
        "publish_capability_result",
    ]:
        assert field in data["task_envelope"]["fields"]

    assert data["loop_controller"]["states"] == [
        "CREATED",
        "READY",
        "RUNNING",
        "CHECKPOINTED",
        "NEEDS_OPERATOR",
        "HUMAN_REVIEW",
        "BLOCKED",
        "CANCELLED",
        "DONE",
    ]
    assert "passing_tests_do_not_imply_automatic_merge" in data["loop_controller"]["controls"]
    assert "target_repository_node_or_device" in data["approval_object"]["bound_to"]
    assert "generic_chat_agreement" in data["approval_object"]["forbidden_inferences"]
    assert "claim_result" in data["evidence_and_delivery"]["worker_evidence_packet"]
    assert data["implementation_sequence"][0]["id"] == "P0"
    assert data["implementation_sequence"][-1]["id"] == "P9"


def test_yaml_contains_memory_layers_and_interface_projection_boundaries() -> None:
    data = load_yaml()
    memory = data["memory_and_state"]

    assert set(memory["authority_classes"]) == {
        "operational_task_run_event_state",
        "approved_private_canonical_facts",
    }
    assert any("Memory Gateway is the only normal entrance" in rule for rule in memory["rules"])
    assert any("Graphify is relationship/dependency orientation" in rule for rule in memory["rules"])
    assert any("MemPalace is semantic retrieval" in rule for rule in memory["rules"])
    assert any("Neither Graphify nor MemPalace can write canon" in rule for rule in memory["rules"])
    assert "Telegram" in data["interface_projections"]["surfaces"]
    assert "must not become canon" in data["interface_projections"]["rule"]


def test_old_plan_docs_point_to_vnext_without_losing_history() -> None:
    build = read(BUILD_PLAN)
    roadmap = read(ROADMAP)
    registry = yaml.safe_load(read(BUILD_PLAN_YAML))

    assert "Historical/foundation notice" in build
    assert "docs/SKELETON_ARCHITECTURE_VNEXT.md" in build
    assert "## Planned Phases" in build
    assert "Historical/foundation notice" in roadmap
    assert "docs/SKELETON_ARCHITECTURE_VNEXT.md" in roadmap
    assert "## Phased Roadmap" in roadmap
    assert registry["superseded_for_target_architecture_by"] == "docs/SKELETON_ARCHITECTURE_VNEXT.md"
    assert registry["phases"][0]["name"] == "source_of_truth_and_boot_route_stabilization"


def test_new_architecture_docs_do_not_contain_private_payload_patterns() -> None:
    combined = read(ARCH_MD) + "\n" + read(ARCH_YAML)
    forbidden_patterns = [
        r"\b\d{1,3}(?:\.\d{1,3}){3}\b",
        r"tailscale\s+ip",
        r"private-home-edge-address",
        r"private-runner-user",
        r"/home/[A-Za-z0-9_.-]+",
        r"ghp_[A-Za-z0-9_]+",
        r"github_pat_[A-Za-z0-9_]+",
        r"BEGIN (?:RSA|OPENSSH|PRIVATE) KEY",
        r"\bSIM\b",
        r"customer data:",
    ]
    for pattern in forbidden_patterns:
        assert not re.search(pattern, combined, flags=re.IGNORECASE)


def test_protected_files_are_not_changed_by_this_task() -> None:
    protected = {
        "BOOT_MANIFEST.yaml",
        "PROJECT_TREE.yaml",
        "OPERATOR_RULES.yaml",
        "CAPABILITY_REGISTRY.yaml",
        "PROVIDER_ROUTING.yaml",
        "MEMORY_ROUTING.yaml",
        "HELPER_REGISTRY.yaml",
    }
    output = subprocess.check_output(["git", "diff", "--name-only"], cwd=ROOT, text=True)
    changed = set(output.splitlines())

    assert protected.isdisjoint(changed)
    assert not any(
        path.startswith(("projects/", "core/", "config/home_edge/", ".github/workflows/"))
        for path in changed
    )
