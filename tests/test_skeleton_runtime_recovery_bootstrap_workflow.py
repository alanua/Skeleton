from __future__ import annotations

import inspect
import pathlib
import re

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "skeleton-runtime-recovery-bootstrap.yml"
TEST_FILE = ROOT / "tests" / "test_skeleton_runtime_recovery_bootstrap_workflow.py"
EXPECTED_SOURCE_BASE_SHA = "f75c8961d480f6d93b514691e6be6613ffa364f5"
OLD_PRE_MERGE_TARGET_SHA = EXPECTED_SOURCE_BASE_SHA
OLD_RUNNER_NAME = "hetzner-agent-runner-1"
ALLOWED_CHANGED_FILES = {
    ".github/workflows/skeleton-runtime-recovery-bootstrap.yml",
    "tests/test_skeleton_runtime_recovery_bootstrap_workflow.py",
}


def load_workflow() -> dict:
    assert WORKFLOW.exists(), "runtime recovery bootstrap workflow is missing"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")) or {}


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def workflow_on(workflow: dict) -> dict:
    return workflow.get("on") or workflow.get(True) or {}


def run_script() -> str:
    workflow = load_workflow()
    return workflow["jobs"]["recover-runtime-checkout"]["steps"][0]["run"]


AUTHORIZATION_MARKER = "Authorization Marker: EXPLICIT_RUNTIME_RECOVERY_BOOTSTRAP_20260802"
CLEANUP_AUTHORIZATION_MARKER = (
    "Authorization Marker: EXPLICIT_RUNTIME_RECOVERY_CLEANUP_TRIGGER_20260803"
)


def expected_triggers() -> dict:
    return {
        "issues": {"types": ["labeled"]},
        "schedule": [{"cron": "*/5 * * * *"}],
        "pull_request": {"types": ["opened", "reopened", "synchronize"]},
    }


def test_workflow_yaml_parses_and_triggers_are_exact_issues_schedule_pull_request() -> None:
    workflow = load_workflow()
    triggers = workflow_on(workflow)

    assert triggers == expected_triggers()
    assert triggers["issues"] == {"types": ["labeled"]}
    assert triggers["schedule"] == [{"cron": "*/5 * * * *"}]
    assert triggers["pull_request"] == {"types": ["opened", "reopened", "synchronize"]}
    assert len(triggers["schedule"]) == 1
    assert set(triggers) == {"issues", "schedule", "pull_request"}
    assert "workflow_dispatch" not in triggers
    assert "repository_dispatch" not in triggers
    assert "push" not in triggers
    assert "inputs" not in workflow_text()


def test_runner_labels_repository_issue_gate_and_source_base_constant_are_fixed() -> None:
    workflow = load_workflow()
    job = workflow["jobs"]["recover-runtime-checkout"]

    issue_authorization = (
        "github.event_name == 'issues' && "
        "github.event.action == 'labeled' && "
        "github.event.issue.number == 2124 && "
        "github.event.issue.state == 'open' && "
        "github.event.label.name == 'risk:green' && "
        "github.event.issue.user.login == 'alanua' && "
        f"contains(github.event.issue.body, '{AUTHORIZATION_MARKER}')"
    )
    pull_request_authorization = (
        "github.event_name == 'pull_request' && "
        "(github.event.action == 'opened' || "
        "github.event.action == 'reopened' || "
        "github.event.action == 'synchronize') && "
        "github.event.pull_request.base.ref == 'main' && "
        "github.event.pull_request.head.ref == 'runner/issue-2145' && "
        "github.event.pull_request.user.login == 'alanua' && "
        f"contains(github.event.pull_request.body, '{CLEANUP_AUTHORIZATION_MARKER}')"
    )
    assert job["if"] == (
        "github.repository == 'alanua/Skeleton' && "
        f"(github.event_name == 'schedule' || ({issue_authorization}) || "
        f"({pull_request_authorization}))"
    )
    assert job["if"].startswith("github.repository == 'alanua/Skeleton' &&")
    assert job["runs-on"] == ["self-hosted", "Linux", "X64"]
    assert OLD_RUNNER_NAME not in workflow_text()
    assert job["env"] == {
        "CANONICAL_REPOSITORY": "alanua/Skeleton",
        "EXPECTED_SOURCE_BASE_SHA": EXPECTED_SOURCE_BASE_SHA,
        "PR_BASE_SHA": "${{ github.event.pull_request.base.sha }}",
        "TARGET_CHECKOUT": "/home/agent/agent-dev/repos/Skeleton",
        "RECOVERY_ROOT": "/home/agent/agent-dev/private-recovery/skeleton",
    }
    assert "EXPECTED_TARGET_SHA" not in job["env"]

    script = run_script()
    assert 'test "${GITHUB_REPOSITORY}" = "${CANONICAL_REPOSITORY}"' in script
    assert 'test "${GITHUB_REF}" = "refs/heads/main"' not in script
    assert 'origin_main_sha="$(git -C "${TARGET_CHECKOUT}" rev-parse origin/main)"' in script
    assert '[[ ! "${origin_main_sha}" =~ ^[0-9a-f]{40}$ ]]' in script
    assert 'test "${GITHUB_SHA}" = "${origin_main_sha}"' in script
    assert 'rev-list --first-parent "${target_sha}"' in script
    assert 'grep -Fx "${EXPECTED_SOURCE_BASE_SHA}" >/dev/null' in script
    assert "EXPECTED_TARGET_SHA" not in script


def test_pull_request_trigger_and_condition_are_exactly_reserved_cleanup_route() -> None:
    workflow = load_workflow()
    job_if = workflow["jobs"]["recover-runtime-checkout"]["if"]

    assert workflow_on(workflow)["pull_request"] == {
        "types": ["opened", "reopened", "synchronize"]
    }
    for required in [
        "github.repository == 'alanua/Skeleton'",
        "github.event_name == 'pull_request'",
        "github.event.action == 'opened'",
        "github.event.action == 'reopened'",
        "github.event.action == 'synchronize'",
        "github.event.pull_request.base.ref == 'main'",
        "github.event.pull_request.head.ref == 'runner/issue-2145'",
        "github.event.pull_request.user.login == 'alanua'",
        f"contains(github.event.pull_request.body, '{CLEANUP_AUTHORIZATION_MARKER}')",
    ]:
        assert required in job_if

    for rejected in [
        "runner/issue-2146",
        "github.event.pull_request.head.sha",
        "github.event.pull_request.merge_commit_sha",
        "github.event.pull_request.head.repo",
        "github.actor",
        "github.base_ref",
        "github.head_ref",
    ]:
        assert rejected not in job_if


def test_job_condition_rejects_wrong_repository_action_base_branch_author_or_marker() -> None:
    job_if = workflow_text()

    assert "github.repository == 'alanua/Skeleton' &&" in job_if
    assert "github.event.pull_request.base.ref == 'main'" in job_if
    assert "github.event.pull_request.head.ref == 'runner/issue-2145'" in job_if
    assert "github.event.pull_request.user.login == 'alanua'" in job_if
    assert CLEANUP_AUTHORIZATION_MARKER in job_if
    assert "github.event.action == 'closed'" not in job_if
    assert "github.event.action == 'edited'" not in job_if
    assert "github.event.pull_request.base.ref !=" not in job_if
    assert "github.event.pull_request.head.ref !=" not in job_if
    assert "github.event.pull_request.user.login !=" not in job_if
    assert "pull_request.body" in job_if


def test_no_arbitrary_issue_input_consumption_or_command_parsing() -> None:
    workflow = load_workflow()
    text = workflow_text()
    script = run_script()

    assert workflow_on(workflow) == expected_triggers()
    assert "github.event.issue.body" in workflow["jobs"]["recover-runtime-checkout"]["if"]
    assert AUTHORIZATION_MARKER in workflow["jobs"]["recover-runtime-checkout"]["if"]
    for forbidden in [
        "github.event.issue.number ==",
        "github.event.label.name ==",
        "github.event.issue.user.login ==",
    ]:
        assert forbidden in workflow["jobs"]["recover-runtime-checkout"]["if"]
    assert "github.event.issue.body" not in script
    assert "Authorization Marker" not in script
    assert re.search(r"issue\.body.*(split|fromjson|contains\([^)]*(run|cmd|command))", text) is None
    assert re.search(r"github\.event\.(inputs|client_payload)", text) is None


def test_target_checkout_origin_and_origin_main_are_verified_before_mutation() -> None:
    script = run_script()

    for snippet in [
        'test "${TARGET_CHECKOUT}" = "/home/agent/agent-dev/repos/Skeleton"',
        'test -d "${TARGET_CHECKOUT}"',
        'test -d "${TARGET_CHECKOUT}/.git"',
        'git -C "${TARGET_CHECKOUT}" remote get-url origin',
        "https://github.com/alanua/Skeleton|https://github.com/alanua/Skeleton.git",
        'git -C "${TARGET_CHECKOUT}" fetch --quiet --prune origin main',
        'origin_main_sha="$(git -C "${TARGET_CHECKOUT}" rev-parse origin/main)"',
        'test "${GITHUB_SHA}" = "${origin_main_sha}"',
        'git -C "${TARGET_CHECKOUT}" rev-list --first-parent "${target_sha}"',
    ]:
        assert snippet in script

    first_stash = script.index("stash push --include-untracked")
    first_checkout = script.index('git -C "${TARGET_CHECKOUT}" checkout -B main "${target_sha}"')
    for destructive in [first_stash, first_checkout]:
        assert script.index('test "${TARGET_CHECKOUT}" = "/home/agent/agent-dev/repos/Skeleton"') < destructive
        assert script.index('git -C "${TARGET_CHECKOUT}" remote get-url origin') < destructive
        assert script.index('origin_main_sha="$(git -C "${TARGET_CHECKOUT}" rev-parse origin/main)"') < destructive
        assert script.index('grep -Fx "${EXPECTED_SOURCE_BASE_SHA}" >/dev/null') < destructive
    assert first_stash < first_checkout


def test_pull_request_target_is_only_fixed_base_sha_and_must_equal_origin_main() -> None:
    workflow = load_workflow()
    script = run_script()

    assert workflow["jobs"]["recover-runtime-checkout"]["env"]["PR_BASE_SHA"] == (
        "${{ github.event.pull_request.base.sha }}"
    )
    assert 'case "${GITHUB_EVENT_NAME}" in' in script
    assert "pull_request)" in script
    assert '[[ ! "${PR_BASE_SHA}" =~ ^[0-9a-f]{40}$ ]]' in script
    assert 'test "${PR_BASE_SHA}" = "${origin_main_sha}"' in script
    assert 'target_sha="${PR_BASE_SHA}"' in script
    assert 'test "${GITHUB_SHA}" = "${origin_main_sha}"' in script
    assert 'target_sha="${GITHUB_SHA}"' in script
    assert script.index('test "${PR_BASE_SHA}" = "${origin_main_sha}"') < script.index(
        'target_sha="${PR_BASE_SHA}"'
    )


def test_pr_head_and_merge_sha_are_never_used_for_target_or_command_execution() -> None:
    text = workflow_text()
    script = run_script()

    for forbidden in [
        "github.event.pull_request.head.sha",
        "github.event.pull_request.merge_commit_sha",
        "github.sha",
        "github.ref",
        "github.head_ref",
        "github.base_ref",
        "refs/pull",
        "pull/",
    ]:
        assert forbidden not in text
    for forbidden in [
        "PR_HEAD",
        "MERGE_SHA",
        "head.sha",
        "merge_commit_sha",
        "refs/pull",
        "github.event.pull_request",
    ]:
        assert forbidden not in script


def test_old_pre_merge_sha_is_ancestry_only_never_final_target() -> None:
    workflow = load_workflow()
    job = workflow["jobs"]["recover-runtime-checkout"]
    script = run_script()

    assert job["env"]["EXPECTED_SOURCE_BASE_SHA"] == OLD_PRE_MERGE_TARGET_SHA
    assert "EXPECTED_TARGET_SHA" not in workflow_text()
    assert 'test "${GITHUB_SHA}" = "${origin_main_sha}"' in script
    assert 'test "${final_sha}" = "${target_sha}"' in script
    assert f'test "${{origin_main_sha}}" = "{OLD_PRE_MERGE_TARGET_SHA}"' not in script
    assert f'test "${{final_sha}}" = "{OLD_PRE_MERGE_TARGET_SHA}"' not in script


def test_source_base_ancestry_and_github_sha_equality_precede_mutation() -> None:
    script = run_script()

    target_from_origin = script.index(
        'origin_main_sha="$(git -C "${TARGET_CHECKOUT}" rev-parse origin/main)"'
    )
    target_format = script.index('[[ ! "${origin_main_sha}" =~ ^[0-9a-f]{40}$ ]]')
    github_sha_present = script.index('[[ -z "${GITHUB_SHA:-}" ]]')
    github_sha_match = script.index('test "${GITHUB_SHA}" = "${origin_main_sha}"')
    ancestry = script.index('rev-list --first-parent "${target_sha}"')
    source_base = script.index('grep -Fx "${EXPECTED_SOURCE_BASE_SHA}" >/dev/null')
    stash = script.index("stash push --include-untracked")
    checkout = script.index('git -C "${TARGET_CHECKOUT}" checkout -B main "${target_sha}"')

    assert target_from_origin < target_format < github_sha_present < github_sha_match
    assert github_sha_match < ancestry < source_base
    assert source_base < stash < checkout


def test_process_quiescence_is_bounded_and_does_not_manage_services() -> None:
    script = run_script()

    assert "for _attempt in $(seq 1 30)" in script
    assert "sleep 2" in script
    assert "pgrep -f '(^|/)runner_poll_github_tasks[.]py([[:space:]]|$)'" in script
    assert 'test "${poller_quiet}" = "true"' in script

    forbidden = re.compile(r"\b(systemctl|service)\s+(stop|restart|kill|disable|enable)\b")
    assert forbidden.search(script) is None
    assert "pkill" not in script
    assert "killall" not in script


def test_preservation_bundle_verify_and_sha256_precede_destructive_commands() -> None:
    script = run_script()

    stash = script.index("stash push --include-untracked")
    recovery_ref = script.index('update-ref "${recovery_ref}" "${stash_sha}"')
    bundle_create = script.index('bundle create "${bundle_path}"')
    bundle_verify = script.index('bundle verify "${bundle_path}"')
    sha256_write = script.index('sha256sum "${bundle_name}" > "${bundle_name}.sha256"')
    sha256_check = script.index('sha256sum --check "${bundle_name}.sha256"')
    checkout = script.index('checkout -B main "${target_sha}"')
    reset = script.index('reset --hard "${target_sha}"')
    clean = script.index("clean -fd")

    assert stash < recovery_ref < bundle_create < bundle_verify < sha256_write < sha256_check
    assert sha256_check < checkout < reset < clean
    assert 'dirty_state_preserved=true' in script[stash:checkout]
    assert 'bundle_verified=true' in script[sha256_check:checkout]
    assert 'git -C "${TARGET_CHECKOUT}" stash pop' not in script
    assert 'git -C "${TARGET_CHECKOUT}" stash apply' not in script


def test_final_checkout_state_is_required_after_cleanup() -> None:
    script = run_script()

    for snippet in [
        'git -C "${TARGET_CHECKOUT}" checkout -B main "${target_sha}"',
        'git -C "${TARGET_CHECKOUT}" reset --hard "${target_sha}"',
        'final_branch="$(git -C "${TARGET_CHECKOUT}" rev-parse --abbrev-ref HEAD)"',
        'final_sha="$(git -C "${TARGET_CHECKOUT}" rev-parse HEAD)"',
        'final_status="$(git -C "${TARGET_CHECKOUT}" status --porcelain --untracked-files=all)"',
        'test "${final_branch}" = "main"',
        'test "${final_sha}" = "${target_sha}"',
        'test -z "${final_status}"',
        "final_clean_state=true",
        "status=DONE",
    ]:
        assert snippet in script


def test_workflow_excludes_unsafe_inputs_network_privilege_and_domain_commands() -> None:
    text = workflow_text().lower()

    for forbidden in [
        "workflow_dispatch",
        "repository_dispatch",
        "pull_request_target",
        "inputs:",
        "curl",
        "wget",
        "ssh",
        "scp",
        "sudo",
        "secrets.",
        "git push",
        "home edge",
        "home_edge",
        "firmware",
        " ota",
        "ota ",
        "device ip",
    ]:
        assert forbidden not in text
    assert re.search(r"\blan\b", text) is None


def test_public_summary_contains_only_aggregate_safe_fields() -> None:
    script = run_script()
    summary = script[script.index("write_summary() {") : script.index("} >> \"${GITHUB_STEP_SUMMARY}\"") + 30]

    assert "GITHUB_STEP_SUMMARY" in summary
    assert "repository_verified=" in summary
    assert "dirty_state_preserved=" in summary
    assert "bundle_verified=" in summary
    assert "final_branch=" in summary
    assert "final_sha=" in summary
    assert "final_clean_state=" in summary
    assert "status=" in summary

    for private_or_dirty_detail in [
        "/home/agent",
        "TARGET_CHECKOUT",
        "RECOVERY_ROOT",
        "bundle_path",
        "bundle_name",
        "sha_path",
        "status_file",
        "git status",
        "stash",
    ]:
        assert private_or_dirty_detail not in summary


def test_changed_files_are_exactly_the_allowed_scope() -> None:
    assert ALLOWED_CHANGED_FILES == {
        ".github/workflows/skeleton-runtime-recovery-bootstrap.yml",
        "tests/test_skeleton_runtime_recovery_bootstrap_workflow.py",
    }
    assert WORKFLOW.exists()
    assert TEST_FILE.exists()


def test_scope_contract_test_is_hermetic() -> None:
    source = inspect.getsource(test_changed_files_are_exactly_the_allowed_scope)

    for forbidden in [
        "git " + "status",
        "subprocess" + ".run",
        "git " + "diff",
        "git " + "ls-files",
        ".codex",
    ]:
        assert forbidden not in source
