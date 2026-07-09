from __future__ import annotations

import pathlib
import re

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "container-package-validation.yml"
DOCS = ROOT / "docs" / "CONTAINER_PACKAGE_VALIDATION.md"


def load_workflow() -> dict:
    assert WORKFLOW.exists(), "container package validation workflow is missing"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")) or {}


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def workflow_on(workflow: dict) -> dict:
    return workflow.get("on") or workflow.get(True) or {}


def test_workflow_has_read_only_permissions_and_safe_triggers() -> None:
    workflow = load_workflow()
    triggers = workflow_on(workflow)

    assert workflow["permissions"] == {"contents": "read"}
    assert "pull_request_target" not in triggers
    assert "pull_request" in triggers
    assert "workflow_dispatch" in triggers

    text = workflow_text().lower()
    for token in [
        "secrets.",
        "actions/cache",
        "upload-artifact",
        "tailscale",
        "hetzner",
        "privileged: true",
        "network_mode: host",
        "environment:",
    ]:
        assert token not in text


def test_pull_request_paths_are_package_scoped() -> None:
    paths = set(workflow_on(load_workflow())["pull_request"]["paths"])
    assert paths == {
        "deploy/n8n/**",
        "deploy/control_board/**",
        "docs/CONTAINER_PACKAGE_VALIDATION.md",
        "tests/test_container_package_validation_workflow.py",
        ".github/workflows/container-package-validation.yml",
    }


def test_manual_dispatch_accepts_only_exact_repository_sha() -> None:
    workflow = load_workflow()
    dispatch_inputs = workflow_on(workflow)["workflow_dispatch"]["inputs"]

    assert set(dispatch_inputs) == {"commit_sha"}
    assert dispatch_inputs["commit_sha"]["required"] is True
    assert dispatch_inputs["commit_sha"]["type"] == "string"

    text = workflow_text()
    assert r"^[0-9a-fA-F]{40}$" in text
    assert 'git cat-file -e "${COMMIT_SHA}^{commit}"' in text
    assert 'checked_out="$(git rev-parse HEAD)"' in text


def test_timeouts_concurrency_and_package_gating_are_enforced() -> None:
    workflow = load_workflow()
    assert workflow["concurrency"]["cancel-in-progress"] is True

    jobs = workflow["jobs"]
    assert jobs["detect-changes"]["timeout-minutes"] == 10
    assert jobs["validate-n8n"]["timeout-minutes"] == 30
    assert jobs["validate-control-board"]["timeout-minutes"] == 35
    assert jobs["validate-n8n"]["if"] == "needs.detect-changes.outputs.n8n == 'true'"
    assert jobs["validate-control-board"]["if"] == "needs.detect-changes.outputs.control_board == 'true'"

    text = workflow_text()
    for snippet in [
        'if [[ -d deploy/n8n ]]; then',
        'if [[ -d deploy/control_board ]]; then',
        "grep -Eq '^deploy/n8n/'",
        "grep -Eq '^deploy/control_board/'",
        'if [[ "$EVENT_NAME" == "workflow_dispatch" ]]',
        'n8n=$n8n_present',
        'control_board=$control_board_present',
        'no registered container packages exist at requested commit',
        'echo "package_presence n8n=$n8n_present control_board=$control_board_present"',
        'echo "package_scope n8n=$n8n control_board=$control_board"',
    ]:
        assert snippet in text


def test_n8n_focused_tests_use_portable_uid_gid_drop_without_unshare() -> None:
    text = workflow_text()

    for snippet in [
        'python3 -m venv "$RUNNER_TEMP/n8n-test-venv"',
        "'PyYAML>=6.0.0,<7.0.0'",
        "'pytest>=8.0.0,<9.0.0'",
        'bash -n "$script"',
        "find tests -type f -name '*n8n*.py'",
        'sudo install -d -m 700 -o 1000 -g 1000 "$uid_gid_test_home" "$uid_gid_test_tmp"',
        'sudo env -i',
        'HOME="$uid_gid_test_home"',
        'TMPDIR="$uid_gid_test_tmp"',
        'PYTHONPATH="$GITHUB_WORKSPACE"',
        'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1',
        'SKELETON_N8N_UID_GID_TEST_ROOT="$uid_gid_test_home"',
        'setpriv --reuid=1000 --regid=1000 --clear-groups --',
        'python3 -m pytest -p no:cacheprovider "${focused_tests[@]}"',
    ]:
        assert snippet in text

    assert "unshare --net" not in text
    assert "sudo --preserve-env" not in text


def test_n8n_compose_uses_synthetic_owner_only_environment() -> None:
    text = workflow_text()

    for snippet in [
        'Prepare synthetic n8n Compose environment',
        'sudo install -d -m 700 -o 1000 -g 1000 "$data_dir" "$backup_dir"',
        'umask 077',
        'N8N_HOST=127.0.0.1',
        'N8N_ENCRYPTION_KEY=synthetic_validation_key_32_chars_minimum',
        'chmod 600 "$env_file"',
        '--env-file "$N8N_VALIDATION_ENV_FILE"',
        'rm -f "$tmp_config" "$N8N_VALIDATION_ENV_FILE"',
        'sudo rm -rf "$N8N_VALIDATION_DATA_DIR" "$N8N_VALIDATION_BACKUP_DIR"',
    ]:
        assert snippet in text

    assert "REPLACE_WITH_PRIVATE" not in text


def test_control_board_uses_explicit_dependencies_and_zero_skip_gate() -> None:
    text = workflow_text()

    for snippet in [
        'python3 -m venv "$RUNNER_TEMP/control-board-venv"',
        "'PyYAML>=6.0.0,<7.0.0'",
        "'pytest>=8.0.0,<9.0.0'",
        "'jsonschema>=4.22.0,<5.0.0'",
        "'fastapi==0.116.1'",
        "'jinja2==3.1.6'",
        "'uvicorn==0.35.0'",
        "'httpx==0.28.1'",
        "find tests -type f \\( -name '*control_board*.py' -o -name '*controlboard*.py' \\)",
        'PYTHONPATH="$GITHUB_WORKSPACE" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1',
        '-p no:cacheprovider',
        'control_board_skips_detected',
        'control_board_test_totals',
    ]:
        assert snippet in text

    assert "pip install -e" not in text
    assert "dependency_file=" not in text
    assert "tomllib" not in text


def test_digest_loopback_health_restart_and_cleanup_controls_are_present() -> None:
    text = workflow_text()

    assert text.count('service.get("privileged")') == 2
    assert text.count('service.get("network_mode") == "host"') == 2
    assert text.count('host_ip not in {"127.0.0.1", "::1"}') == 2
    assert text.count('target == "/var/run/docker.sock"') == 2
    assert text.count('source in {"/", "/etc", "/home", "/root", "/var"}') == 2

    assert text.count('trap cleanup EXIT') == 2
    assert text.count('down --volumes --remove-orphans') == 2
    assert 'docker network prune --force --filter' in text

    assert '@sha256:[0-9a-f]{64}' in text
    assert 'n8n_image_digest' in text
    assert 'control_board_base_image_digest' in text
    assert 'docker compose -f "$compose_file" build' in text
    assert 'n8n_health_state' in text
    assert 'n8n_restart_persistence' in text
    assert 'control_board_health_state' in text

    assert text.count('ps --all --format json') == 3
    assert text.count('bad_health=sum(1 for record in records if record.get("Health", "") not in {"", "healthy"})') == 3
    assert text.count('running=sum(1 for record in records if record.get("State") == "running")') == 3
    assert text.count('[[ "$total" != "0" && "$running" == "$total" && "$bad_health" == "0" ]]') == 3


def test_logs_are_limited_to_aggregate_status_and_no_sensitive_dump() -> None:
    text = workflow_text()

    for forbidden in [
        'cat "$tmp_config"',
        'docker compose -f "$compose_file" logs',
        'printenv',
        'cat "$N8N_VALIDATION_ENV_FILE"',
    ]:
        assert forbidden not in text

    for expected in [
        'echo "package_presence',
        'echo "package_scope',
        'echo "n8n_image_digest',
        'echo "control_board_base_image_digest',
        'echo "n8n_health_state',
        'echo "control_board_health_state',
        'echo "control_board_test_totals',
    ]:
        assert expected in text


def test_documentation_matches_validation_boundary() -> None:
    docs = DOCS.read_text(encoding="utf-8")
    for phrase in [
        'permissions: contents: read',
        'does not use `pull_request_target`',
        'does not read repository, environment, or organization secrets',
        'does not write caches or upload artifacts',
        'Manual dispatch accepts one input, `commit_sha`',
        'exact 40-character SHA',
        'package-presence and package-scope booleans',
        'portable UID/GID drop',
        'synthetic owner-only Compose environment',
        'explicit pinned dependency set',
        'every non-empty Compose health value to be `healthy`',
        'loopback-only',
        'unconditional cleanup',
        'zero Control Board skips',
    ]:
        assert phrase in docs
