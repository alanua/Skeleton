from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ARCH_MD = ROOT / "docs" / "SKELETON_ARCHITECTURE_VNEXT.md"
ARCH_YAML = ROOT / "docs" / "SKELETON_ARCHITECTURE_VNEXT.yaml"
BUILD_PLAN = ROOT / "docs" / "SKELETON_BUILD_PLAN.md"
BUILD_PLAN_YAML = ROOT / "docs" / "SKELETON_BUILD_PLAN.yaml"
ROADMAP = ROOT / "docs" / "DEVELOPMENT_DEPARTMENT_ROADMAP.md"


EXPECTED_TOP_LEVEL_KEYS = [
    "schema",
    "status",
    "identity",
    "authority_principle",
    "governance_kernel",
    "loop_controller",
    "task_envelope",
    "approval_object",
    "execution_broker",
    "home_edge_node",
    "evidence_and_delivery",
    "memory_and_state",
    "interface_projections",
    "supersession",
    "implementation_sequence",
    "decisions",
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_yaml() -> dict:
    return yaml.safe_load(read(ARCH_YAML))


def test_markdown_contains_required_status_sections_and_diagrams() -> None:
    text = read(ARCH_MD)

    assert "Status: ACTIVE_ARCHITECTURE / OPERATOR_APPROVED / HUMAN_GATED" in text
    assert "becomes repository canon only when this PR is reviewed and merged" in text
    assert "GitHub `main` remains public control/code/policy canon" in text
    assert "Canonical private SQLite remains the private source of truth" in text
    assert "`BOOT_MANIFEST.yaml` remains the entrypoint" in text

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
        "## Supersession Map",
        "## Implementation Sequence",
        "## Required Decisions",
    ]:
        assert section in text

    assert "Operator\n  -> Governance Kernel\n  -> Loop Controller\n  -> Runner Execution Broker" in text
    assert "GitHub public canon + private SQLite canon\n  -> Memory Gateway" in text


def test_yaml_has_strict_expected_top_level_keys_and_core_authority() -> None:
    data = load_yaml()

    assert list(data) == EXPECTED_TOP_LEVEL_KEYS
    assert data["schema"] == "skeleton.architecture_vnext.v1"
    assert data["status"]["code"] == "ACTIVE_ARCHITECTURE / OPERATOR_APPROVED / HUMAN_GATED"
    assert data["status"]["public_control_canon"] == {"repo": "alanua/Skeleton", "ref": "main"}
    assert data["status"]["private_canon"].startswith("Canonical private SQLite")
    assert data["status"]["boot_entrypoint"] == "BOOT_MANIFEST.yaml"
    assert data["authority_principle"]["order"][0] == "current explicit operator instruction for the task"
    assert data["authority_principle"]["order"][-1] == "model inference and chat memory"
    assert "cannot silently override" in data["authority_principle"]["invariant"]


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


def test_yaml_contains_home_edge_identity_boundary_and_status() -> None:
    node = load_yaml()["home_edge_node"]

    assert node["node_id"] == "home-edge-01"
    assert node["role"] == "universal controlled local execution node"
    assert node["controller_path"] == [
        "Skeleton Governance/Loop",
        "Hetzner Runner",
        "Tailscale transport",
        "home-edge-01",
    ]
    assert node["status"] == "architecture-approved"
    assert "unmerged" in node["implementation_status"]["pr_1254"]
    assert node["authority_boundary"] == "execution_plane_not_control_plane_not_canon"
    assert "no_public_unrestricted_shell_or_agent_api" in node["boundaries"]
    assert "green_yellow_red_action_classes" in node["boundaries"]


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


def test_supersession_entries_distinguish_status_classes() -> None:
    entries = load_yaml()["supersession"]["entries"]
    by_source = {entry["source"]: entry for entry in entries}

    assert by_source["Exoskeleton documents"]["status"] == "historical_foundation_evidence"
    assert by_source["docs/SKELETON_BUILD_PLAN.md"]["status"] == "merged_canon_foundation"
    assert by_source["issue #1088 Rules Rebuild"]["status"] == "active_programme"
    assert by_source["issue #1066 / PR #1070"]["status"] == "review_evidence"
    assert by_source["issue #1089 unpublished Stage 0"]["status"] == "review_evidence"
    assert by_source["issue #1182 Loop Engineering"]["status"] == "operator_approved_on_merge"
    assert by_source["issue #1253 / PR #1254"]["status"] == "unmerged_pr_evidence"
    assert by_source["issue #1256"]["status"] == "planned_programme"


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
    protected = [
        "BOOT_MANIFEST.yaml",
        "PROJECT_TREE.yaml",
        "OPERATOR_RULES.yaml",
        "CAPABILITY_REGISTRY.yaml",
        "PROVIDER_ROUTING.yaml",
        "MEMORY_ROUTING.yaml",
        "HELPER_REGISTRY.yaml",
    ]
    # Runtime check uses git when the focused test is run from a normal checkout.
    import subprocess

    output = subprocess.check_output(
        ["git", "diff", "--name-only"],
        cwd=ROOT,
        text=True,
    )
    changed = set(output.splitlines())
    for path in protected:
        assert path not in changed
    assert not any(path.startswith(("projects/", "core/", "config/home_edge/", ".github/workflows/")) for path in changed)
