from __future__ import annotations

import pathlib
import re
import subprocess

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "skeleton-runtime-recovery-bootstrap.yml"
EXPECTED_SHA = "f75c8961d480f6d93b514691e6be6613ffa364f5"
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


def test_workflow_yaml_parses_and_trigger_is_exact_push_main_path_only() -> None:
    workflow = load_workflow()
    triggers = workflow_on(workflow)

    assert set(triggers) == {"push"}
    assert triggers["push"]["branches"] == ["main"]
    assert triggers["push"]["paths"] == [
        ".github/workflows/skeleton-runtime-recovery-bootstrap.yml"
    ]
    assert "workflow_dispatch" not in triggers
    assert "repository_dispatch" not in triggers
    assert "inputs" not in workflow_text()


def test_runner_labels_repository_and_sha_constants_are_fixed() -> None:
    workflow = load_workflow()
    job = workflow["jobs"]["recover-runtime-checkout"]

    assert job["if"] == "github.repository == 'alanua/Skeleton' && github.ref == 'refs/heads/main'"
    assert job["runs-on"] == ["self-hosted", "hetzner-agent-runner-1"]
    assert job["env"] == {
        "CANONICAL_REPOSITORY": "alanua/Skeleton",
        "EXPECTED_SOURCE_BASE_SHA": EXPECTED_SHA,
        "EXPECTED_TARGET_SHA": EXPECTED_SHA,
        "TARGET_CHECKOUT": "/home/agent/agent-dev/repos/Skeleton",
        "RECOVERY_ROOT": "/home/agent/agent-dev/private-recovery/skeleton",
    }

    script = run_script()
    assert 'test "${GITHUB_REPOSITORY}" = "${CANONICAL_REPOSITORY}"' in script
    assert 'test "${GITHUB_REF}" = "refs/heads/main"' in script
    assert '[[ "${GITHUB_SHA}" != "${EXPECTED_SOURCE_BASE_SHA}" ]]' in script
    assert 'git -C "${TARGET_CHECKOUT}" fetch --quiet --no-tags origin "${GITHUB_SHA}"' in script
    assert 'rev-list --first-parent "${GITHUB_SHA}"' in script
    assert 'grep -Fx "${EXPECTED_SOURCE_BASE_SHA}" >/dev/null' in script


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
        'test "${origin_main_sha}" = "${EXPECTED_TARGET_SHA}"',
    ]:
        assert snippet in script

    first_destructive = script.index('git -C "${TARGET_CHECKOUT}" checkout -B main origin/main')
    assert script.index('test "${origin_main_sha}" = "${EXPECTED_TARGET_SHA}"') < first_destructive


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
    checkout = script.index('checkout -B main origin/main')
    reset = script.index("reset --hard origin/main")
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
        'final_branch="$(git -C "${TARGET_CHECKOUT}" rev-parse --abbrev-ref HEAD)"',
        'final_sha="$(git -C "${TARGET_CHECKOUT}" rev-parse HEAD)"',
        'final_status="$(git -C "${TARGET_CHECKOUT}" status --porcelain --untracked-files=all)"',
        'test "${final_branch}" = "main"',
        'test "${final_sha}" = "${EXPECTED_TARGET_SHA}"',
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
    result = subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=ROOT,
        text=True,
        check=True,
        capture_output=True,
    )
    changed = set()
    for line in result.stdout.splitlines():
        if not line:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path.startswith(".codex"):
            continue
        changed.add(path)

    assert changed == ALLOWED_CHANGED_FILES
