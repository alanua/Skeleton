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
    assert 'n8n_changed=true' in text
    assert 'control_board_changed=true' in text
    assert 'maintenance_changed=true' in text
    assert 'n8n=true' in text
    assert 'control_board=true' in text
    assert 'if [[ "$EVENT_NAME" == "workflow_dispatch" ]]' in text


def test_package_presence_gates_maintenance_and_manual_dispatch_scope() -> None:
    text = workflow_text()

    assert 'if [[ -d deploy/n8n ]]; then' in text
    assert 'if [[ -d deploy/control_board ]]; then' in text
    assert 'n8n=$n8n_present' in text
    assert 'control_board=$control_board_present' in text
    assert 'no registered container packages exist at requested commit' in text
    assert (
        '[[ "$maintenance_changed" == "true" && "$n8n_changed" == "false" '
        '&& "$n8n_present" == "true" ]]'
    ) in text
    assert (
        '[[ "$maintenance_changed" == "true" && "$control_board_changed" == "false" '
        '&& "$control_board_present" == "true" ]]'
    ) in text
    assert 'echo "package_presence n8n=$n8n_present control_board=$control_board_present"' in text
    assert 'echo "package_scope n8n=$n8n control_board=$control_board"' in text


def test_n8n_required_validation_steps_are_present() -> None:
    text = workflow_text()

    required = [
        "python3 -m venv \"$RUNNER_TEMP/n8n-test-venv\"",
        "\"$RUNNER_TEMP/n8n-test-venv/bin/python3\" -m pip install 'PyYAML>=6.0.0,<7.0.0' 'pytest>=8.0.0,<9.0.0'",
        "bash -n \"$script\"",
        "find tests -type f -name '*n8n*.py'",
        "sudo env -i",
        "HOME=\"$uid_gid_test_home\"",
        "PYTHONPATH=\"$GITHUB_WORKSPACE\"",
        "SKELETON_N8N_UID_GID_TEST_ROOT=\"$uid_gid_test_home\"",
        "unshare --net --setgid 1000 --setuid 1000 --",
        "python3 -m pytest -p no:cacheprovider \"${focused_tests[@]}\"",
        "docker compose -f \"$compose_file\" config --quiet",
        "docker compose -f \"$compose_file\" config --images",
        "@sha256:[0-9a-f]{64}",
        "docker compose -f \"$compose_file\" up -d --remove-orphans",
        "deadline=$((SECONDS + 120))",
        "n8n_health_state",
        "docker compose -f \"$compose_file\" restart",
        "deadline=$((SECONDS + 120))",
        "n8n_restart_persistence",
    ]
    for snippet in required:
        assert snippet in text


def test_n8n_focused_tests_have_bounded_dependencies_and_uid_gid_environment() -> None:
    text = workflow_text()

    assert "No module named pytest" not in text
    assert "pytest>=8.0.0,<9.0.0" in text
    assert "PyYAML>=6.0.0,<7.0.0" in text
    assert "sudo --preserve-env" not in text
    assert "env -i" in text
    assert "--net" in text
    assert "--setgid 1000 --setuid 1000" in text
    assert "SKELETON_N8N_UID_GID_TEST_ROOT" in text


def test_control_board_required_validation_steps_are_present() -> None:
    text = workflow_text()

    required = [
        "python3 -m venv \"$RUNNER_TEMP/control-board-venv\"",
        "pyproject = tomllib.loads(pathlib.Path(\"pyproject.toml\").read_text())",
        "pyproject[\"project\"].get(\"dependencies\", [])",
        "pyproject[\"project\"][\"optional-dependencies\"][\"dev\"]",
        "python3 -m pip install -r \"$dependency_file\"",
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


def test_control_board_dev_dependencies_install_without_editable_package_discovery() -> None:
    text = workflow_text()

    assert "pip install -e" not in text
    assert "'.[dev]'" not in text
    assert "tomllib" in text
    assert "dependency_file=\"$(mktemp)\"" in text
    assert "python3 -m pip install -r \"$dependency_file\"" in text
    assert "control_board_skips_detected" in text


def test_loopback_and_docker_safety_checks_are_present_for_both_packages() -> None:
    text = workflow_text()

    assert text.count('service.get("privileged")') == 2
    assert text.count('service.get("network_mode") == "host"') == 2
    assert text.count('host_ip not in {"127.0.0.1", "::1"}') == 2
    assert text.count('target == "/var/run/docker.sock"') == 2
    assert text.count('source in {"/", "/etc", "/home", "/root", "/var"}') == 2


def test_compose_health_waits_require_all_services_running_and_healthy() -> None:
    text = workflow_text()

    assert text.count('docker compose -f "$compose_file" ps --all --format json') == 3
    assert text.count('bad_health=sum(1 for record in records if record.get("Health", "") not in {"", "healthy"})') == 3
    assert text.count('running=sum(1 for record in records if record.get("State") == "running")') == 3
    assert text.count('[[ "$total" != "0" && "$running" == "$total" && "$bad_health" == "0" ]]') == 3
    assert "sleep 10" not in text
    assert 'after_restart="$(docker compose' not in text


def test_logs_are_limited_to_aggregate_status_digests_health_and_totals() -> None:
    text = workflow_text()

    assert "cat \"$tmp_config\"" not in text
    assert "docker compose -f \"$compose_file\" logs" not in text
    assert "printenv" not in text
    assert 'echo "package_presence n8n=$n8n_present control_board=$control_board_present"' in text
    assert 'echo "package_scope n8n=$n8n control_board=$control_board"' in text
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
        "package-presence and package-scope booleans",
        "every registered package present at the requested commit",
        "fails closed if the changed package directory is unexpectedly absent",
        "every non-empty Compose health value to be `healthy`",
        "loopback-only",
        "unconditional cleanup",
        "zero Control Board skips",
    ]:
        assert phrase in docs
