from __future__ import annotations

import pathlib
import re

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "runner-controller-host-self-audit-bridge.yml"


def load_workflow() -> dict:
    assert WORKFLOW.exists(), "runner-controller host self-audit bridge workflow is missing"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")) or {}


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def workflow_on(workflow: dict) -> dict:
    return workflow.get("on") or workflow.get(True) or {}


def test_workflow_is_manual_read_only_and_registered_repository_only() -> None:
    workflow = load_workflow()
    triggers = workflow_on(workflow)

    assert set(triggers) == {"workflow_dispatch"}
    assert "pull_request" not in triggers
    assert "pull_request_target" not in triggers
    assert workflow["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert workflow["jobs"]["authorize-current-pr-head"]["if"] == (
        "github.repository == 'alanua/Skeleton'"
    )


def test_dispatch_inputs_are_exact_pr_number_and_head_sha_only() -> None:
    inputs = workflow_on(load_workflow())["workflow_dispatch"]["inputs"]

    assert set(inputs) == {"pull_request_number", "head_sha"}
    assert inputs["pull_request_number"]["required"] is True
    assert inputs["pull_request_number"]["type"] == "string"
    assert inputs["head_sha"]["required"] is True
    assert inputs["head_sha"]["type"] == "string"

    text = workflow_text()
    assert r"[1-9][0-9]{0,8}" in text
    assert r"[0-9a-f]{40}" in text
    assert "head_sha must be an exact lowercase 40-character commit SHA" in text


def test_authorization_reads_current_pr_head_from_github_before_host_job() -> None:
    workflow = load_workflow()
    jobs = workflow["jobs"]
    authorize = jobs["authorize-current-pr-head"]
    host = jobs["host-self-audit"]
    text = workflow_text()

    assert list(jobs) == ["authorize-current-pr-head", "host-self-audit"]
    assert authorize["runs-on"] == "ubuntu-latest"
    assert host["needs"] == "authorize-current-pr-head"
    assert host["if"] == "needs.authorize-current-pr-head.outputs.authorized_head_sha == inputs.head_sha"

    for snippet in [
        'f"https://api.github.com{path}"',
        'f"/repos/{repository}/pulls/{pr_number}"',
        'pull.get("state") != "open"',
        'pull.get("base", {}).get("ref") != "main"',
        'pull.get("head", {}).get("repo", {}).get("full_name") != repository',
        "current_head_sha != expected_head_sha",
        "github current pull request head is not a full lowercase SHA",
        "authorized_current_pr_head=",
    ]:
        assert snippet in text

    assert "merge_commit_sha" not in text.split("Run public-safe read-only host self-audit")[0]


def test_authorization_enforces_exact_two_file_pr_scope() -> None:
    text = workflow_text()

    for snippet in [
        '".github/workflows/runner-controller-host-self-audit-bridge.yml"',
        '"tests/test_runner_controller_host_self_audit_bridge_workflow.py"',
        'f"/repos/{repository}/pulls/{pr_number}/files?per_page=100&page={page}"',
        "changed_files.add(filename)",
        "if changed_files != allowed_files:",
        "pull request must contain exactly the host self-audit bridge workflow",
        "page > 30",
    ]:
        assert snippet in text


def test_host_job_targets_registered_self_hosted_runner_and_is_strictly_read_only() -> None:
    host = load_workflow()["jobs"]["host-self-audit"]
    text = workflow_text()
    host_text = text.split("host-self-audit:", 1)[1]

    assert host["runs-on"] == ["self-hosted", "Linux", "X64", "hetzner-agent-runner-1"]
    assert host["timeout-minutes"] == 10
    assert host["env"]["TARGET_CHECKOUT"] == "/home/agent/agent-dev/repos/Skeleton"
    assert host["env"]["RUNNER_WORKTREES_ROOT"] == "/home/agent/agent-dev/worktrees/skeleton"

    for expected in [
        'test -d "${TARGET_CHECKOUT}"',
        'test -d "${TARGET_CHECKOUT}/.git"',
        'test -d "${RUNNER_WORKTREES_ROOT}"',
        "git --version >/dev/null",
        "python3 --version >/dev/null",
        'uname -srm >/dev/null',
        'id -u >/dev/null',
        'id -g >/dev/null',
        'df -Pk "${RUNNER_TEMP}" >/dev/null',
        'git -C "${TARGET_CHECKOUT}" rev-parse HEAD >/dev/null',
        'git -C "${TARGET_CHECKOUT}" status --short --untracked-files=no >/dev/null',
        "pgrep -af '(^|/)runner_poll_github_tasks[.]py([[:space:]]|$)'",
    ]:
        assert expected in host_text

    forbidden_patterns = [
        r"\bsudo\b",
        r"\bsu\b",
        r"\bchmod\b",
        r"\bchown\b",
        r"\binstall\b",
        r"\bmkdir\b",
        r"\btouch\b",
        r"\brm\b",
        r"\bmv\b",
        r"\bcp\b",
        r"\brsync\b",
        r"\btee\b",
        r"\bcurl\b",
        r"\bwget\b",
        r"\bssh\b",
        r"\bscp\b",
        r"\bdocker\b",
        r"\bsystemctl\s+(start|stop|restart|reload|enable|disable|mask|unmask)\b",
        r"\bgit\s+(-C\s+\"\$\{TARGET_CHECKOUT\}\"\s+)?(fetch|pull|push|checkout|reset|clean|stash|merge|rebase|commit|tag|update-ref)\b",
    ]
    for pattern in forbidden_patterns:
        assert re.search(pattern, host_text) is None, pattern


def test_host_summary_is_public_safe_and_contains_exact_head_sha() -> None:
    text = workflow_text()

    for snippet in [
        "audit_status=DONE",
        "authorized_pr_number=${AUTHORIZED_PR_NUMBER}",
        "authorized_head_sha=${AUTHORIZED_HEAD_SHA}",
        "external_side_effects_executed=false",
        "private_evidence_exposed=false",
        'test "${AUTHORIZED_HEAD_SHA}" = "${{ inputs.head_sha }}"',
        'test "${AUTHORIZED_HEAD_SHA}" != "${{ github.event.pull_request.merge_commit_sha }}"',
    ]:
        assert snippet in text

    for forbidden in [
        "printenv",
        "env |",
        "cat /",
        "ls -la /home",
        "secrets.",
        "upload-artifact",
        "actions/cache",
        "pull-requests: write",
        "contents: write",
        "id-token: write",
    ]:
        assert forbidden not in text
