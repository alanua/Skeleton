from __future__ import annotations

import inspect
import pathlib
import re

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "runner-controller-host-self-audit-bridge.yml"
TEST_FILE = ROOT / "tests" / "test_runner_controller_host_self_audit_bridge_workflow.py"
EXPECTED_HEAD_SHA = "8f29994bcdcde3891a545cc39dcaab1dde7f3d92"
ALLOWED_CHANGED_FILES = {
    ".github/workflows/runner-controller-host-self-audit-bridge.yml",
    "tests/test_runner_controller_host_self_audit_bridge_workflow.py",
}


def load_workflow() -> dict:
    assert WORKFLOW.exists(), "runner controller host self-audit bridge workflow is missing"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")) or {}


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def workflow_on(workflow: dict) -> dict:
    return workflow.get("on") or workflow.get(True) or {}


def bridge_job() -> dict:
    return load_workflow()["jobs"]["host-self-audit-bridge"]


def bridge_script() -> str:
    return bridge_job()["steps"][0]["run"]


def test_workflow_yaml_parses_and_trigger_is_only_pull_request_labeled() -> None:
    workflow = load_workflow()
    triggers = workflow_on(workflow)

    assert triggers == {"pull_request": {"types": ["labeled"]}}
    assert workflow["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert set(workflow["jobs"]) == {"host-self-audit-bridge"}
    assert "workflow_dispatch" not in triggers
    assert "repository_dispatch" not in triggers
    assert "pull_request_target" not in triggers
    assert "push" not in triggers
    assert "schedule" not in triggers


def test_job_is_gated_to_exact_draft_pr_label_head_sha_and_two_file_count() -> None:
    job = bridge_job()
    job_if = job["if"]

    assert job["runs-on"] == ["self-hosted", "Linux", "X64"]
    assert job["timeout-minutes"] == 5
    assert job_if == (
        "github.repository == 'alanua/Skeleton' && "
        "github.event_name == 'pull_request' && "
        "github.event.action == 'labeled' && "
        "github.event.label.name == 'host:self-audit' && "
        "github.event.pull_request.draft == true && "
        "github.event.pull_request.base.ref == 'main' && "
        f"github.event.pull_request.head.sha == '{EXPECTED_HEAD_SHA}' && "
        "github.event.pull_request.changed_files == 2"
    )
    for required in [
        "github.repository == 'alanua/Skeleton'",
        "github.event.action == 'labeled'",
        "github.event.label.name == 'host:self-audit'",
        "github.event.pull_request.draft == true",
        "github.event.pull_request.base.ref == 'main'",
        f"github.event.pull_request.head.sha == '{EXPECTED_HEAD_SHA}'",
        "github.event.pull_request.changed_files == 2",
    ]:
        assert required in job_if


def test_environment_pins_exact_head_and_allowed_two_file_scope() -> None:
    env = bridge_job()["env"]

    assert env == {
        "CANONICAL_REPOSITORY": "alanua/Skeleton",
        "EXPECTED_HEAD_SHA": EXPECTED_HEAD_SHA,
        "REQUIRED_LABEL": "host:self-audit",
        "REQUIRED_BASE_REF": "main",
        "REQUIRED_CHANGED_FILES": "2",
        "ALLOWED_FILE_1": ".github/workflows/runner-controller-host-self-audit-bridge.yml",
        "ALLOWED_FILE_2": "tests/test_runner_controller_host_self_audit_bridge_workflow.py",
    }
    assert {env["ALLOWED_FILE_1"], env["ALLOWED_FILE_2"]} == ALLOWED_CHANGED_FILES


def test_script_is_metadata_only_and_reports_no_external_side_effects() -> None:
    script = bridge_script()

    for snippet in [
        'event_path = os.environ.get("GITHUB_EVENT_PATH", "")',
        'repository.get("full_name") == os.environ["CANONICAL_REPOSITORY"]',
        'event.get("action") == "labeled"',
        'label.get("name") == os.environ["REQUIRED_LABEL"]',
        'pull_request.get("draft") is True',
        'base.get("ref") == os.environ["REQUIRED_BASE_REF"]',
        'head.get("sha") == os.environ["EXPECTED_HEAD_SHA"]',
        'str(pull_request.get("changed_files")) == os.environ["REQUIRED_CHANGED_FILES"]',
        'summary.write("external_side_effects_executed=false\\n")',
        'print("RESULT: DONE")',
        'print("external_side_effects_executed=false")',
    ]:
        assert snippet in script

    assert "subprocess" not in script
    assert "os.system" not in script
    assert "urllib" not in script
    assert "requests" not in script


def test_workflow_excludes_checkout_secrets_privilege_network_and_mutation_commands() -> None:
    text = workflow_text().lower()

    for forbidden in [
        "actions/checkout",
        "actions/github-script",
        "upload-artifact",
        "secrets.",
        "sudo",
        "ssh",
        "scp",
        "curl",
        "wget",
        "gh ",
        "git push",
        "git add",
        "git commit",
        "git checkout",
        "git reset",
        "git clean",
        "systemctl",
        "service ",
        "docker",
        "privileged: true",
        "network_mode: host",
        "pull_request_target",
        "workflow_dispatch",
    ]:
        assert forbidden not in text
    assert re.search(r"\b(fetch|merge|rm -rf|chmod|chown|pkill|killall)\b", text) is None


def test_public_summary_contains_only_safe_aggregate_fields() -> None:
    script = bridge_script()
    summary = script[script.index('summary.write("repository=alanua/Skeleton') :]

    for snippet in [
        "repository=alanua/Skeleton",
        "bridge_status=metadata_verified",
        "expected_head_sha=",
        "changed_files=2",
        "external_side_effects_executed=false",
    ]:
        assert snippet in summary

    for private_or_unbounded_detail in [
        "/home/",
        "GITHUB_EVENT_PATH",
        "GITHUB_WORKSPACE",
        "GITHUB_TOKEN",
        "runner",
        "hostname",
        "ip",
        "path=",
        "pull_request",
        "label",
    ]:
        assert private_or_unbounded_detail not in summary


def test_changed_files_are_exactly_the_allowed_scope() -> None:
    assert ALLOWED_CHANGED_FILES == {
        ".github/workflows/runner-controller-host-self-audit-bridge.yml",
        "tests/test_runner_controller_host_self_audit_bridge_workflow.py",
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
