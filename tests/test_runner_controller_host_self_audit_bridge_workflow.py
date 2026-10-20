from __future__ import annotations

import pathlib
import re

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "runner-controller-host-self-audit-bridge.yml"
EXPECTED_MAIN_SHA = "8f29994bcdcde3891a545cc39dcaab1dde7f3d92"
ALLOWED_CHANGED_FILES = {
    ".github/workflows/runner-controller-host-self-audit-bridge.yml",
    "tests/test_runner_controller_host_self_audit_bridge_workflow.py",
}


def load_workflow() -> dict:
    assert WORKFLOW.exists(), "runner-controller host self-audit bridge workflow is missing"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")) or {}


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def workflow_on(workflow: dict) -> dict:
    return workflow.get("on") or workflow.get(True) or {}


def run_script() -> str:
    workflow = load_workflow()
    return workflow["jobs"]["audit-runner-controller-host"]["steps"][0]["run"]


def test_semantic_01_trigger_is_exact_pull_request_labeled_only() -> None:
    triggers = workflow_on(load_workflow())

    assert triggers == {"pull_request": {"types": ["labeled"]}}
    text = workflow_text()
    for forbidden in [
        "workflow_dispatch",
        "pull_request_target",
        "repository_dispatch",
        "\n  issues:",
        "\n  push:",
        "\n  schedule:",
        "inputs:",
    ]:
        assert forbidden not in text


def test_semantic_02_permissions_are_repository_read_only() -> None:
    workflow = load_workflow()

    assert workflow["permissions"] == {"contents": "read"}
    text = workflow_text().lower()
    for forbidden in [
        "contents: write",
        "actions: write",
        "checks: write",
        "issues: write",
        "pull-requests: write",
        "id-token: write",
        "secrets.",
        "github_token",
        "github-token",
    ]:
        assert forbidden not in text


def test_semantic_03_job_condition_is_exact_labeled_pr_authorization_gate() -> None:
    job_if = load_workflow()["jobs"]["audit-runner-controller-host"]["if"]

    assert job_if == (
        "github.repository == 'alanua/Skeleton' && "
        "github.event.action == 'labeled' && "
        "github.event.label.name == 'host:self-audit' && "
        "github.event.pull_request.state == 'open' && "
        "github.event.pull_request.base.ref == 'main' && "
        f"github.event.pull_request.base.sha == '{EXPECTED_MAIN_SHA}' && "
        "github.event.pull_request.user.login == 'alanua'"
    )
    for forbidden in [
        "github.actor",
        "github.base_ref",
        "github.head_ref",
        "github.event.pull_request.head.sha",
        "github.event.pull_request.merge_commit_sha",
        "contains(",
        "!=",
    ]:
        assert forbidden not in job_if


def test_semantic_04_self_hosted_runner_and_timeout_are_fixed() -> None:
    job = load_workflow()["jobs"]["audit-runner-controller-host"]

    assert job["runs-on"] == ["self-hosted", "Linux", "X64"]
    assert job["timeout-minutes"] == 8
    assert job["name"] == "Audit runner-controller host trust anchors"


def test_semantic_05_trust_anchor_environment_is_exact_and_pinned_to_main_sha() -> None:
    job = load_workflow()["jobs"]["audit-runner-controller-host"]

    assert job["env"] == {
        "CANONICAL_REPOSITORY": "alanua/Skeleton",
        "EXPECTED_CANONICAL_MAIN_SHA": EXPECTED_MAIN_SHA,
        "EVENT_ACTION": "${{ github.event.action }}",
        "TARGET_CHECKOUT": "/home/agent/agent-dev/repos/Skeleton",
    }
    assert re.search(r"\b[0-9a-f]{40}\b", workflow_text()).group(0) == EXPECTED_MAIN_SHA


def test_semantic_06_registered_checkout_identity_is_audited_without_checkout_action() -> None:
    text = workflow_text()
    script = run_script()

    assert "actions/checkout" not in text
    assert 'test "${TARGET_CHECKOUT}" = "/home/agent/agent-dev/repos/Skeleton"' in script
    assert 'test -d "${TARGET_CHECKOUT}/.git"' in script
    assert 'remote get-url origin' in script
    assert "https://github.com/alanua/Skeleton|https://github.com/alanua/Skeleton.git" in script
    assert 'current_branch="$(git -C "${TARGET_CHECKOUT}" rev-parse --abbrev-ref HEAD)"' in script
    assert 'test "${current_branch}" = "main"' in script


def test_semantic_07_canonical_main_sha_is_checked_against_head_and_origin_only() -> None:
    script = run_script()

    assert 'checkout_head_sha="$(git -C "${TARGET_CHECKOUT}" rev-parse HEAD)"' in script
    assert 'test "${checkout_head_sha}" = "${EXPECTED_CANONICAL_MAIN_SHA}"' in script
    assert 'checkout_origin_main_sha="$(git -C "${TARGET_CHECKOUT}" rev-parse refs/remotes/origin/main)"' in script
    assert 'test "${checkout_origin_main_sha}" = "${EXPECTED_CANONICAL_MAIN_SHA}"' in script
    for forbidden in [
        "git -C \"${TARGET_CHECKOUT}\" fetch",
        "git -C \"${TARGET_CHECKOUT}\" pull",
        "git -C \"${TARGET_CHECKOUT}\" checkout",
        "git -C \"${TARGET_CHECKOUT}\" reset",
        "git -C \"${TARGET_CHECKOUT}\" clean",
        "git -C \"${TARGET_CHECKOUT}\" merge",
    ]:
        assert forbidden not in script


def test_semantic_08_clean_state_audit_does_not_disclose_private_paths_or_diff() -> None:
    script = run_script()

    assert 'status --porcelain --untracked-files=all' in script
    assert 'test -z "${clean_status}"' in script
    for forbidden in [
        "git diff",
        "git status --short",
        "ls -",
        "find ",
        "cat ",
        "sed ",
        "printenv",
        "env |",
        "set -x",
    ]:
        assert forbidden not in script


def test_semantic_09_no_privilege_device_network_or_persistence_mutation() -> None:
    text = workflow_text().lower()

    for forbidden in [
        "sudo",
        "systemctl",
        "service ",
        "docker",
        "podman",
        "tailscale",
        "hetzner",
        "mount ",
        "chmod ",
        "chown ",
        "install ",
        "mkdir ",
        "rm ",
        "mv ",
        "cp ",
        "curl ",
        "wget ",
        "ssh ",
        "gh ",
        "api.github.com",
        "upload-artifact",
        "actions/cache",
      ]:
        assert forbidden not in text


def test_semantic_10_summary_reports_matrix_pass_without_live_side_effects() -> None:
    script = run_script()

    for key in [
        "semantic_matrix_01_trigger=pull_request_labeled",
        "semantic_matrix_02_repository=${repository_verified}",
        "semantic_matrix_03_event=${event_verified}",
        "semantic_matrix_04_permissions=contents_read",
        "semantic_matrix_05_runner=self_hosted_linux_x64",
        "semantic_matrix_06_checkout=${checkout_verified}",
        "semantic_matrix_07_remote=${remote_verified}",
        "semantic_matrix_08_canonical_sha=head:${head_verified},origin:${origin_verified}",
        "semantic_matrix_09_clean_state=${clean_state_verified}",
        "semantic_matrix_10_no_live_side_effects=",
        "expected_canonical_main_sha=${EXPECTED_CANONICAL_MAIN_SHA}",
        "status=${status}",
    ]:
        assert key in script
    assert "mutation_performed=false" in script
    assert "status=DONE" in script


def test_exact_two_file_scope() -> None:
    assert {
        ".github/workflows/runner-controller-host-self-audit-bridge.yml",
        "tests/test_runner_controller_host_self_audit_bridge_workflow.py",
    } == ALLOWED_CHANGED_FILES
