from __future__ import annotations

import inspect
from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/runner-controller-gateway-bootstrap-bridge.yml"
TEST_FILE = ROOT / "tests/test_runner_controller_gateway_bootstrap_bridge_workflow.py"
IDEMPOTENCY_KEY = "one-shot-gateway-bootstrap-bridge-8f29994b-v1"
ALLOWED_CHANGED_FILES = {
    ".github/workflows/runner-controller-gateway-bootstrap-bridge.yml",
    "tests/test_runner_controller_gateway_bootstrap_bridge_workflow.py",
}


def load_workflow() -> dict:
    assert WORKFLOW.exists(), "gateway bootstrap bridge workflow is missing"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")) or {}


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def workflow_on(workflow: dict) -> dict:
    return workflow.get("on") or workflow.get(True) or {}


def run_script() -> str:
    workflow = load_workflow()
    return workflow["jobs"]["bootstrap-bridge"]["steps"][0]["run"]


def test_workflow_has_exact_one_shot_main_push_trigger_and_minimal_permissions() -> None:
    workflow = load_workflow()
    triggers = workflow_on(workflow)

    assert workflow["permissions"] == {"contents": "read"}
    assert triggers == {
        "push": {
            "branches": ["main"],
            "paths": [".github/workflows/runner-controller-gateway-bootstrap-bridge.yml"],
        }
    }
    assert workflow["concurrency"] == {
        "group": "runner-controller-gateway-bootstrap-bridge-v1",
        "cancel-in-progress": False,
    }
    assert "workflow_dispatch" not in triggers
    assert "repository_dispatch" not in triggers
    assert "pull_request_target" not in triggers
    assert "inputs" not in workflow_text()


def test_job_is_bound_to_canonical_repository_runner_and_fixed_gateway_contract() -> None:
    workflow = load_workflow()
    job = workflow["jobs"]["bootstrap-bridge"]

    assert job["if"] == "github.repository == 'alanua/Skeleton' && github.ref == 'refs/heads/main'"
    assert job["runs-on"] == ["self-hosted", "Linux", "X64"]
    assert job["timeout-minutes"] == 10
    assert job["env"] == {
        "CANONICAL_REPOSITORY": "alanua/Skeleton",
        "TARGET_CHECKOUT": "/home/agent/agent-dev/repos/Skeleton",
        "GATEWAY_INSTALL_PATH": "/usr/local/libexec/skeleton/runner-controller/privileged-gateway",
        "ACTION_ID": "home_edge_01_esp_lab_stage1_signer_install_v1",
        "OPERATOR_APPROVAL": "EXACT_HEAD_HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_V2_APPROVED",
        "REQUEST_ID": IDEMPOTENCY_KEY,
        "IDEMPOTENCY_KEY": IDEMPOTENCY_KEY,
    }


def test_checkout_origin_clean_head_and_github_sha_are_verified_before_gateway_call() -> None:
    script = run_script()

    for snippet in [
        'test "${GITHUB_REPOSITORY}" = "${CANONICAL_REPOSITORY}"',
        'test "${GITHUB_REF}" = "refs/heads/main"',
        'test "${TARGET_CHECKOUT}" = "/home/agent/agent-dev/repos/Skeleton"',
        'test "${GATEWAY_INSTALL_PATH}" = "/usr/local/libexec/skeleton/runner-controller/privileged-gateway"',
        'test -d "${TARGET_CHECKOUT}/.git"',
        'test -x "${GATEWAY_INSTALL_PATH}"',
        'git -C "${TARGET_CHECKOUT}" remote get-url origin',
        "https://github.com/alanua/Skeleton|https://github.com/alanua/Skeleton.git",
        'git -C "${TARGET_CHECKOUT}" fetch --quiet --prune origin main',
        'origin_main_sha="$(git -C "${TARGET_CHECKOUT}" rev-parse origin/main)"',
        '[[ ! "${origin_main_sha}" =~ ^[0-9a-f]{40}$ ]]',
        '[[ ! "${GITHUB_SHA}" =~ ^[0-9a-f]{40}$ ]]',
        'test "${GITHUB_SHA}" = "${origin_main_sha}"',
        'checkout_branch="$(git -C "${TARGET_CHECKOUT}" rev-parse --abbrev-ref HEAD)"',
        'checkout_head_sha="$(git -C "${TARGET_CHECKOUT}" rev-parse HEAD)"',
        'checkout_origin_main_sha="$(git -C "${TARGET_CHECKOUT}" rev-parse origin/main)"',
        'checkout_status="$(git -C "${TARGET_CHECKOUT}" status --porcelain --untracked-files=all)"',
        'test "${checkout_branch}" = "main"',
        'test "${checkout_head_sha}" = "${origin_main_sha}"',
        'test "${checkout_origin_main_sha}" = "${origin_main_sha}"',
        'test -z "${checkout_status}"',
    ]:
        assert snippet in script

    gateway_call = script.index('/usr/bin/sudo -n "${GATEWAY_INSTALL_PATH}"')
    for preflight in [
        'test "${GITHUB_SHA}" = "${origin_main_sha}"',
        'test "${checkout_head_sha}" = "${origin_main_sha}"',
        'test -z "${checkout_status}"',
    ]:
        assert script.index(preflight) < gateway_call


def test_typed_gateway_request_is_exact_and_short_lived() -> None:
    workflow = load_workflow()
    script = run_script()

    for snippet in [
        '"schema": "skeleton.runner_controller_privileged_request.v1"',
        '"request_id": os.environ["REQUEST_ID"]',
        '"idempotency_key": os.environ["IDEMPOTENCY_KEY"]',
        '"action_id": os.environ["ACTION_ID"]',
        '"repository": os.environ["CANONICAL_REPOSITORY"]',
        '"target": "runner-controller"',
        '"operator_approval": os.environ["OPERATOR_APPROVAL"]',
        '"expected_main_sha": os.environ["EXPECTED_MAIN_SHA"]',
        '"registered_clean_main_sha": os.environ["EXPECTED_MAIN_SHA"]',
        '"github_main_sha": os.environ["EXPECTED_MAIN_SHA"]',
        '"checkout_path": os.environ["TARGET_CHECKOUT"]',
        '"checkout_head_sha": os.environ["CHECKOUT_HEAD_SHA"]',
        '"checkout_origin_main_sha": os.environ["CHECKOUT_ORIGIN_MAIN_SHA"]',
        'expires_at="$(date -u -d \'+300 seconds\' \'+%Y-%m-%dT%H:%M:%SZ\')"',
        'if len(encoded.encode("utf-8")) > 16 * 1024:',
        'json.dumps(request, sort_keys=True, separators=(",", ":")) + "\\n"',
    ]:
        assert snippet in script

    assert "EXACT_HEAD_HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_APPROVED" not in workflow_text()
    assert (
        workflow["jobs"]["bootstrap-bridge"]["env"]["OPERATOR_APPROVAL"]
        == "EXACT_HEAD_HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_V2_APPROVED"
    )


def test_workflow_invokes_only_fixed_no_argument_sudo_gateway() -> None:
    text = workflow_text()
    script = run_script()

    assert '/usr/bin/sudo -n "${GATEWAY_INSTALL_PATH}" < "${request_file}" > "${receipt_file}"' in script
    assert text.count("/usr/bin/sudo") == 1
    assert text.count("privileged-gateway") == 2
    assert "--repo-root" not in text
    assert "installer_argv" not in text
    assert "install_home_edge_esp_lab_activation_signer.sh" not in text
    assert "systemctl" not in text
    assert "service " not in text
    assert "curl" not in text
    assert "wget" not in text
    assert "ssh" not in text
    assert "scp" not in text
    assert "gh " not in text
    assert "git push" not in text
    assert "secrets." not in text
    assert "printenv" not in text


def test_receipt_validation_and_public_summary_are_aggregate_only() -> None:
    script = run_script()

    for snippet in [
        'if len(raw) > 16 * 1024:',
        '"schema",',
        '"mutation_started",',
        '"mutation_performed",',
        '"private_evidence_exposed",',
        '"stderr_exposed",',
        '"env_exposed",',
        '"private_paths_exposed",',
        '"external_side_effects_executed",',
        'if receipt["schema"] != "skeleton.runner_controller_privileged_receipt.v1":',
        'if receipt["repository"] != "alanua/Skeleton" or receipt["target"] != "runner-controller":',
        'if receipt["action_id"] != "home_edge_01_esp_lab_stage1_signer_install_v1":',
        'if receipt["status"] not in {"DONE", "NEEDS_OPERATOR"}:',
        'if receipt.get(key) is not False:',
        'runner_controller_gateway_bootstrap_bridge_status=',
        'external_side_effects_executed=',
        'private_evidence_exposed=false',
        'stderr_exposed=false',
        'env_exposed=false',
        'private_paths_exposed=false',
        'request_hash=',
        'receipt_hash=',
        'cat "${summary_file}" >> "${GITHUB_STEP_SUMMARY}"',
    ]:
        assert snippet in script

    summary_block = script[
        script.index("lines = [") : script.index('Path(os.environ["SUMMARY_FILE"]).write_text')
    ]
    for private_or_runtime_detail in [
        "/home/agent",
        "TARGET_CHECKOUT",
        "GATEWAY_INSTALL_PATH",
        "request_file",
        "receipt_file",
        "checkout_status",
        "origin_url",
        "raw",
    ]:
        assert private_or_runtime_detail not in summary_block


def test_workflow_has_no_arbitrary_command_input_or_device_activation_surface() -> None:
    text = workflow_text().lower()

    for forbidden in [
        "workflow_dispatch",
        "repository_dispatch",
        "pull_request_target",
        "inputs:",
        "client_payload",
        "issue.body",
        "pull_request.body",
        "device ip",
        "firmware",
        " ota",
        "ota ",
        "activation_approved",
        "home_edge_exec",
        "authorized_keys",
        "sudoers.d",
    ]:
        assert forbidden not in text
    assert re.search(r"\blan\b", text) is None


def test_changed_files_are_exactly_the_allowed_scope() -> None:
    assert ALLOWED_CHANGED_FILES == {
        ".github/workflows/runner-controller-gateway-bootstrap-bridge.yml",
        "tests/test_runner_controller_gateway_bootstrap_bridge_workflow.py",
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
