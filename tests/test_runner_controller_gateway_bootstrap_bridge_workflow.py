from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/runner-controller-gateway-bootstrap-bridge.yml"
EXPECTED_MAIN_SHA = "8f29994bcdcde3891a545cc39dcaab1dde7f3d92"
EXPECTED_BRANCH = "manual/gateway-bootstrap-bridge-20260827"
EXPECTED_MARKER = "EXACT_HEAD_RUNNER_CONTROLLER_GATEWAY_BOOTSTRAP_BRIDGE_APPROVED"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _workflow() -> dict[str, object]:
    loaded = yaml.safe_load(_text())
    assert isinstance(loaded, dict)
    return loaded


def test_trigger_permissions_and_runner_are_exact() -> None:
    workflow = _workflow()
    assert workflow["on"] == {"pull_request": {"types": ["labeled"]}}
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    assert set(jobs) == {"bootstrap-gateway"}
    job = jobs["bootstrap-gateway"]
    assert job["runs-on"] == ["self-hosted", "Linux", "X64"]
    assert job["timeout-minutes"] == 8


def test_operator_gate_binds_exact_base_branch_and_current_head() -> None:
    job = _workflow()["jobs"]["bootstrap-gateway"]
    condition = job["if"]
    assert "github.repository == 'alanua/Skeleton'" in condition
    assert "github.event.pull_request.base.ref == 'main'" in condition
    assert f"github.event.pull_request.base.sha == '{EXPECTED_MAIN_SHA}'" in condition
    assert f"github.event.pull_request.head.ref == '{EXPECTED_BRANCH}'" in condition
    assert "github.event.pull_request.user.login == 'alanua'" in condition
    assert "github.event.label.name == 'gateway-bootstrap-approved'" in condition
    assert f"Authorization Marker: {EXPECTED_MARKER}" in condition
    assert "contains(github.event.pull_request.body, github.event.pull_request.head.sha)" in condition


def test_root_mutation_is_only_exact_bootstrap_install_and_execution() -> None:
    text = _text()
    assert text.count("/usr/bin/sudo -n") == 2
    assert (
        "/usr/bin/sudo -n /usr/bin/install -D -o root -g root -m 0555 --"
        in text
    )
    assert (
        '/usr/bin/sudo -n "${PROTECTED_BOOTSTRAP_PATH}"' in text
    )
    assert '--repo-root "${TARGET_CHECKOUT}"' in text
    assert '--expected-main-sha "${EXPECTED_MAIN_SHA}"' in text
    assert 'PROTECTED_BOOTSTRAP_PATH: /usr/local/libexec/skeleton/runner-controller/bootstrap/install_runner_controller_privileged_gateway.sh' in text
    assert '/usr/bin/sudo -n "${TARGET_CHECKOUT}' not in text
    assert 'sudo bash' not in text
    assert 'bash -c' not in text
    assert 'eval ' not in text
    assert 'os.system' not in text


def test_source_is_exact_git_object_and_main_is_reverified_before_root() -> None:
    text = _text()
    assert f"EXPECTED_MAIN_SHA: {EXPECTED_MAIN_SHA}" in text
    assert "status --porcelain=v1 --untracked-files=all" in text
    assert "rev-parse 'HEAD^{commit}'" in text
    assert "rev-parse 'origin/main^{commit}'" in text
    assert "https://github.com/alanua/Skeleton.git" in text
    assert "GIT_CONFIG_GLOBAL=/dev/null" in text
    assert "GIT_CONFIG_NOSYSTEM=1" in text
    assert "GIT_TERMINAL_PROMPT=0" in text
    assert 'ls-tree "${EXPECTED_MAIN_SHA}" -- "${BOOTSTRAP_SOURCE_PATH}"' in text
    assert 'cat-file blob "${bootstrap_tree_blob}" > "${staged}"' in text
    assert 'hash-object --no-filters -- "${staged}"' in text
    assert 'test "${tree_mode}" = 100755' in text
    assert 'test "${staged_blob}" = "${bootstrap_tree_blob}"' in text


def test_bridge_has_no_other_trigger_secret_or_device_authority() -> None:
    text = _text().lower()
    forbidden = (
        "workflow_dispatch",
        "repository_dispatch",
        "pull_request_target",
        "schedule:",
        "secrets.",
        "curl ",
        "wget ",
        "scp ",
        "permitrootlogin",
        "home-edge",
        "esp_lab_stage1_activation",
        "firmware",
        "ota",
    )
    for token in forbidden:
        assert token not in text
    assert "ssh=disabled_not_configured" in text


def test_public_summary_is_aggregate_only() -> None:
    text = _text()
    summary_start = text.index('echo "status=${status}"')
    summary_end = text.index('} >> "${GITHUB_STEP_SUMMARY}"', summary_start)
    summary = text[summary_start:summary_end]
    assert "TARGET_CHECKOUT" not in summary
    assert "PROTECTED_BOOTSTRAP_PATH" not in summary
    assert "temp_root" not in summary
    assert "bootstrap.stderr" not in summary
    assert "bootstrap.stdout" not in summary
    assert "bootstrap_tree_blob" in summary
    assert "expected_main_sha" in summary
