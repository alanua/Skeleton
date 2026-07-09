from __future__ import annotations

import pathlib
import re

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "container-package-validation.yml"
DOCS = ROOT / "docs" / "CONTAINER_PACKAGE_VALIDATION.md"


def load_workflow() -> dict:
    assert WORKFLOW.exists(), "container package validation workflow is missing"
    return yaml.safe_load(WORKFLOW.read_text()) or {}


def workflow_text() -> str:
    return WORKFLOW.read_text()


def workflow_on(workflow: dict) -> dict:
    return workflow.get("on") or workflow.get(True) or {}


def test_workflow_has_read_only_permissions_and_safe_triggers() -> None:
    workflow = load_workflow()
    triggers = workflow_on(workflow)

    assert workflow["permissions"] == {"contents": "read"}
    assert "pull_request_target" not in triggers
    assert "pull_request" in triggers
    assert "workflow_dispatch" in triggers

    text = workflow_text()
    forbidden = [
        "secrets.",
        "actions/cache",
        "upload-artifact",
        "ssh",
        "tailscale",
        "hetzner",
        "privileged: true",
        "network_mode: host",
        "environment:",
    ]
    for token in forbidden:
        assert token not in text.lower()


def test_pull_request_paths_are_package_scoped() -> None:
    paths = set(workflow_on(load_workflow())["pull_request"]["paths"])

    assert paths == {
        "deploy/n8n/**",
        "deploy/control_board/**",
        "docs/CONTAINER_PACKAGE_VALIDATION.md",
        "tests/test_container_package_validation_workflow.py",
        ".github/workflows/container-package-validation.yml",
    }


def test_manual_dispatch_accepts_only_exact_sha_and_checks_repository_commit() -> None:
    workflow = load_workflow()
    dispatch_inputs = workflow_on(workflow)["workflow_dispatch"]["inputs"]

    assert set(dispatch_inputs) == {"commit_sha"}
    assert dispatch_inputs["commit_sha"]["required"] is True
    assert dispatch_inputs["commit_sha"]["type"] == "string"

    text = workflow_text()
    assert r"^[0-9a-fA-F]{40}$" in text
    assert 'git cat-file -e "${COMMIT_SHA}^{commit}"' in text
    assert 'checked_out="$(git rev-parse HEAD)"' in text


def test_timeout_concurrency_cancellation_and_cleanup_are_enforced() -> None:
    workflow = load_workflow()

    assert workflow["concurrency"]["cancel-in-progress"] is True
    jobs = workflow["jobs"]
    assert jobs["detect-changes"]["timeout-minutes"] == 10
    assert jobs["validate-n8n"]["timeout-minutes"] == 30
    assert jobs["validate-control-board"]["timeout-minutes"] == 35

    text = workflow_text()
    assert text.count("trap cleanup EXIT") == 2
    assert text.count("docker compose -f \"$compose_file\" down --volumes --remove-orphans") == 2
    assert text.count("rm -f \"$tmp_config\"") == 2
    assert "docker network prune --force --filter \"label=com.docker.compose.project=$COMPOSE_PROJECT_NAME\"" in text


def test_changed_path_detection_runs_only_relevant_packages() -> None:
    jobs = load_workflow()["jobs"]

    assert jobs["validate-n8n"]["if"] == "needs.detect-changes.outputs.n8n == 'true'"
    assert jobs["validate-control-board"]["if"] == "needs.detect-changes.outputs.control_board == 'true'"

    text = workflow_text()
    assert 'grep -Eq \'^deploy/n8n/\'' in text
    assert 'grep -Eq \'^deploy/control_board/\'' in text
    assert 'n8n=true' in text
    assert 'control_board=true' in text
    assert 'if [[ "$EVENT_NAME" == "workflow_dispatch" ]]' in text


def test_n8n_required_validation_steps_are_present() -> None:
    text = workflow_text()

    required = [
        "bash -n \"$script\"",
        "find tests -type f -name '*n8n*.py'",
        "python3 -m pytest \"${focused_tests[@]}\"",
        "docker compose -f \"$compose_file\" config --quiet",
        "docker compose -f \"$compose_file\" config --images",
        "@sha256:[0-9a-f]{64}",
        "docker compose -f \"$compose_file\" up -d --remove-orphans",
        "deadline=$((SECONDS + 120))",
        "n8n_health_state",
        "docker compose -f \"$compose_file\" restart",
        "n8n_restart_persistence",
    ]
    for snippet in required:
        assert snippet in text


def test_control_board_required_validation_steps_are_present() -> None:
    text = workflow_text()

    required = [
        "python3 -m venv \"$RUNNER_TEMP/control-board-venv\"",
        "python3 -m pip install -e '.[dev]'",
        "find tests -type f \\( -name '*control_board*.py' -o -name '*controlboard*.py' \\)",
        "python3 -m pytest \"${focused_tests[@]}\" -rs",
        "control_board_skips_detected",
        "control_board_test_totals",
        "control_board_base_image_digest",
        "docker compose -f \"$compose_file\" build",
        "control_board_health_state",
    ]
    for snippet in required:
        assert snippet in text


def test_loopback_and_docker_safety_checks_are_present_for_both_packages() -> None:
    text = workflow_text()

    assert text.count('service.get("privileged")') == 2
    assert text.count('service.get("network_mode") == "host"') == 2
    assert text.count('host_ip not in {"127.0.0.1", "::1"}') == 2
    assert text.count('target == "/var/run/docker.sock"') == 2
    assert text.count('source in {"/", "/etc", "/home", "/root", "/var"}') == 2


def test_logs_are_limited_to_aggregate_status_digests_health_and_totals() -> None:
    text = workflow_text()

    assert "cat \"$tmp_config\"" not in text
    assert "docker compose -f \"$compose_file\" logs" not in text
    assert "printenv" not in text
    assert re.search(r"echo \"n8n_image_digest", text)
    assert re.search(r"echo \"control_board_base_image_digest", text)
    assert re.search(r"echo \"n8n_health_state", text)
    assert re.search(r"echo \"control_board_health_state", text)
    assert re.search(r"echo \"control_board_test_totals", text)


def test_documentation_matches_validation_boundary() -> None:
    docs = DOCS.read_text()

    for phrase in [
        "permissions: contents: read",
        "does not use `pull_request_target`",
        "does not read repository, environment, or organization secrets",
        "does not write caches or upload artifacts",
        "Manual dispatch accepts one input, `commit_sha`",
        "exact 40-character SHA",
        "loopback-only",
        "unconditional cleanup",
        "zero Control Board skips",
    ]:
        assert phrase in docs
