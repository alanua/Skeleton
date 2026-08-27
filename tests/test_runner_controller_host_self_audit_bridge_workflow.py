from __future__ import annotations

import pathlib
import re

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "runner-controller-host-self-audit-bridge.yml"
EXPECTED_HEAD_SHA = "8f29994bcdcde3891a545cc39dcaab1dde7f3d92"
ALLOWED_SCOPE = {
    ".github/workflows/runner-controller-host-self-audit-bridge.yml",
    "tests/test_runner_controller_host_self_audit_bridge_workflow.py",
}


def load_workflow() -> dict:
    assert WORKFLOW.exists(), "host self-audit bridge workflow is missing"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")) or {}


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def workflow_on(workflow: dict) -> dict:
    return workflow.get("on") or workflow.get(True) or {}


def run_script() -> str:
    return load_workflow()["jobs"]["host-self-audit"]["steps"][0]["run"]


def test_workflow_is_labeled_exact_head_and_read_only() -> None:
    workflow = load_workflow()
    triggers = workflow_on(workflow)
    job = workflow["jobs"]["host-self-audit"]

    assert workflow["name"] == "Runner Controller Host Self-Audit Bridge"
    assert triggers == {"issues": {"types": ["labeled"]}}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": "runner-controller-host-self-audit-bridge",
        "cancel-in-progress": False,
    }
    assert job["runs-on"] == ["self-hosted", "Linux", "X64"]
    assert job["timeout-minutes"] == 8
    assert job["env"]["EXPECTED_HEAD_SHA"] == EXPECTED_HEAD_SHA
    assert job["env"]["CANONICAL_REPOSITORY"] == "alanua/Skeleton"
    assert job["env"]["TARGET_CHECKOUT"] == "/home/agent/agent-dev/repos/Skeleton"
    assert job["env"]["CODEX_WORKTREE_PREFIX"] == "/home/agent/agent-dev/worktrees/skeleton"

    condition = job["if"]
    assert condition == (
        "github.repository == 'alanua/Skeleton' && "
        "github.event.issue.state == 'open' && "
        "github.event.label.name == 'host-self-audit:read-only' && "
        "github.event.issue.user.login == 'alanua' && "
        "contains(github.event.issue.body, 'Authorization Marker: EXACT_HEAD_HOST_SELF_AUDIT_BRIDGE_V1')"
    )


def test_bridge_script_distinguishes_host_ownership_from_codex_namespace_remap() -> None:
    script = run_script()

    for snippet in [
        'test "${GITHUB_SHA}" = "${EXPECTED_HEAD_SHA}"',
        'case "${GITHUB_WORKSPACE:-}" in',
        '"${CODEX_WORKTREE_PREFIX}"/*)',
        "CODEX_WORKTREE_NAMESPACE_REMAP",
        'uid_map="$(sed -n \'1p\' /proc/self/uid_map)"',
        'gid_map="$(sed -n \'1p\' /proc/self/gid_map)"',
        "NON_HOST_USER_NAMESPACE",
        "remapped_user_namespace",
        'stat -c \'%U:%G:%u:%g\' "${TARGET_CHECKOUT}"',
        'stat -c \'%U:%G:%u:%g\' "${TARGET_CHECKOUT}/.git"',
        "CHECKOUT_OWNER_MISMATCH",
        'host_ownership="${checkout_owner}"',
    ]:
        assert snippet in script


def test_bridge_verifies_real_checkout_origin_and_exact_head_without_mutation() -> None:
    script = run_script()

    for snippet in [
        'test "${TARGET_CHECKOUT}" = "/home/agent/agent-dev/repos/Skeleton"',
        'test -d "${TARGET_CHECKOUT}/.git"',
        'git -C "${TARGET_CHECKOUT}" remote get-url origin',
        "https://github.com/alanua/Skeleton|https://github.com/alanua/Skeleton.git",
        'git -C "${TARGET_CHECKOUT}" fetch --quiet --dry-run origin main',
        'origin_main="$(git -C "${TARGET_CHECKOUT}" rev-parse origin/main)"',
        'checkout_head="$(git -C "${TARGET_CHECKOUT}" rev-parse HEAD)"',
        'test "${origin_main}" = "${EXPECTED_HEAD_SHA}"',
        'test "${checkout_head}" = "${EXPECTED_HEAD_SHA}"',
        "HOST_SELF_AUDIT_READ_ONLY_VERIFIED",
    ]:
        assert snippet in script

    assert "git pull" not in script
    assert "git merge" not in script
    assert "git checkout" not in script
    assert "git reset" not in script


def test_bridge_locks_expected_two_file_scope_for_draft_pr() -> None:
    workflow = load_workflow()
    script = run_script()

    assert set(workflow["jobs"]["host-self-audit"]["env"]["EXPECTED_PR_SCOPE"].split()) == ALLOWED_SCOPE
    assert 'git -C "${GITHUB_WORKSPACE}" diff --name-only "${EXPECTED_HEAD_SHA}...HEAD"' in script
    assert "UNEXPECTED_PR_SCOPE" in script
    assert "scope_status=exact_two_files" in script


def test_workflow_excludes_live_side_effects_privilege_and_sensitive_paths() -> None:
    text = workflow_text().lower()

    forbidden_tokens = [
        "pull_request_target",
        "workflow_dispatch",
        "repository_dispatch",
        "secrets.",
        "actions/checkout",
        "upload-artifact",
        "sudo",
        "systemctl",
        "chmod",
        "chown",
        "install ",
        "mount",
        "umount",
        "docker",
        "gh ",
        "git push",
        "git add",
        "git commit",
        "git merge",
        "git checkout",
        "git reset",
        "rm -",
        "/boot",
        "/dev/disk",
        "/dev/sd",
        "/dev/nvme",
        "/etc/sudoers",
        "activation_signer",
        "esp-lab",
        "esptool",
    ]
    for token in forbidden_tokens:
        assert token not in text

    assert "mutation_performed=false" in text
    assert "external_side_effects_executed=false" in text


def test_summary_exposes_only_aggregate_public_safe_receipt_fields() -> None:
    script = run_script()
    summary_block = re.search(r"finish\(\) \{(?P<body>.*?)\n\}", script, re.DOTALL)
    assert summary_block is not None
    body = summary_block.group("body")

    for expected in [
        "status=${status}",
        "reason=${reason}",
        "expected_head_sha=${EXPECTED_HEAD_SHA}",
        "checkout_head=${checkout_head}",
        "origin_main=${origin_main}",
        "host_ownership=${host_ownership}",
        "namespace_map=${namespace_map}",
        "scope_status=${scope_status}",
        "mutation_performed=false",
        "external_side_effects_executed=false",
    ]:
        assert expected in body

    for forbidden in ["printenv", "env |", "cat ", "GITHUB_TOKEN", "ACTIONS_ID_TOKEN"]:
        assert forbidden not in script
