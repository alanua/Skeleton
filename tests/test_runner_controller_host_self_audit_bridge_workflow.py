from __future__ import annotations

from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/runner-controller-host-self-audit-bridge.yml"
EXPECTED_MAIN_SHA = "8f29994bcdcde3891a545cc39dcaab1dde7f3d92"
EXPECTED_BRANCH = "runner/issue-3514"
EXPECTED_LABEL = "runner-controller-host-self-audit-approved"
EXPECTED_MARKER = "EXACT_HEAD_RUNNER_CONTROLLER_HOST_SELF_AUDIT_APPROVED"


EXPECTED_CANONICAL_BLOBS = {
    "CAPABILITY_REGISTRY.yaml": ("100644", "444", "b264a0d60afadc9fae35a351852f3aade7bcc359"),
    "RUNNER_PRIVILEGED_ACTIONS.yaml": ("100644", "444", "b70e4c415013171fb1d064c5d5170ad8037118e8"),
    "core/home_edge/esp_lab_stage1_signer_install.py": (
        "100644",
        "444",
        "84a2c7aaa6b7f89c285094d564b986154b4d8468",
    ),
    "core/runner_controller_privileged_gateway.py": (
        "100644",
        "444",
        "5cd6b3876e97f636f619548adf87ad47601e7507",
    ),
    "core/runner_controller_privileged_gateway_hardening.py": (
        "100644",
        "444",
        "ce970cc54edc6ded84b1696dce9777d93a315b80",
    ),
    "docs/RUNNER_CONTROLLER_PRIVILEGED_GATEWAY.md": (
        "100644",
        "444",
        "3407fa04bb3dd8f6f6b10c297df0d7d52fa354ba",
    ),
    "schemas/runner_controller_privileged_receipt.schema.json": (
        "100644",
        "444",
        "ac265f22c2e2a06eee7746c65b2dff9d3a5326f0",
    ),
    "schemas/runner_controller_privileged_request.schema.json": (
        "100644",
        "444",
        "9b5ab01f5c1277493bfd34523dfff3dc57f338be",
    ),
    "scripts/install_runner_controller_privileged_gateway.sh": (
        "100755",
        "555",
        "5b909c12ba57d5e71b236d709a54b761552b39e8",
    ),
    "scripts/runner_controller_privileged_gateway.py": (
        "100755",
        "555",
        "766b7a6cd1cec315638e040e86e335626d6173d5",
    ),
}


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _workflow() -> dict[str, object]:
    loaded = yaml.safe_load(_text())
    assert isinstance(loaded, dict)
    return loaded


def _anchor_matrix() -> dict[str, tuple[str, str, str, str]]:
    matrix = _workflow()["jobs"]["host-self-audit"]["env"]["ANCHOR_MATRIX"]
    assert isinstance(matrix, str)
    anchors: dict[str, tuple[str, str, str, str]] = {}
    for line in matrix.splitlines():
        source_mode, install_mode, source_path, installed_path, blob = line.split()
        anchors[source_path] = (source_mode, install_mode, installed_path, blob)
    return anchors


def _git_ls_tree(path: str) -> tuple[str, str]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-tree", EXPECTED_MAIN_SHA, "--", path],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    line = result.stdout.strip()
    assert line
    meta, tree_path = line.split("\t", 1)
    mode, kind, blob = meta.split()
    assert kind == "blob"
    assert tree_path == path
    return mode, blob


def test_trigger_permissions_and_runner_are_exact_pull_request_labeled() -> None:
    workflow = _workflow()
    assert workflow["on"] == {"pull_request": {"types": ["labeled"]}}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": "runner-controller-host-self-audit-bridge",
        "cancel-in-progress": False,
    }
    jobs = workflow["jobs"]
    assert set(jobs) == {"host-self-audit"}
    job = jobs["host-self-audit"]
    assert job["runs-on"] == ["self-hosted", "Linux", "X64"]
    assert job["timeout-minutes"] == 6
    assert "pull_request_target" not in _text()
    assert "workflow_dispatch" not in _text()
    assert "repository_dispatch" not in _text()
    assert "schedule:" not in _text()


def test_event_gate_matrix_binds_exact_safe_pr_authorization() -> None:
    condition = _workflow()["jobs"]["host-self-audit"]["if"]
    required_fragments = {
        "github.event_name == 'pull_request'",
        "github.event.action == 'labeled'",
        "github.repository == 'alanua/Skeleton'",
        "github.event.pull_request.base.repo.full_name == 'alanua/Skeleton'",
        "github.event.pull_request.head.repo.full_name == 'alanua/Skeleton'",
        "github.event.pull_request.base.ref == 'main'",
        f"github.event.pull_request.base.sha == '{EXPECTED_MAIN_SHA}'",
        f"github.event.pull_request.head.ref == '{EXPECTED_BRANCH}'",
        "github.event.pull_request.user.login == 'alanua'",
        f"github.event.label.name == '{EXPECTED_LABEL}'",
        f"Authorization Marker: {EXPECTED_MARKER}",
        "contains(github.event.pull_request.body, github.event.pull_request.head.sha)",
    }
    for fragment in required_fragments:
        assert fragment in condition
    assert " || " not in condition
    assert condition.count("&&") == len(required_fragments) - 1


def test_canonical_gateway_trust_anchor_matrix_matches_exact_main_blobs() -> None:
    assert _workflow()["jobs"]["host-self-audit"]["env"]["EXPECTED_MAIN_SHA"] == EXPECTED_MAIN_SHA
    anchors = _anchor_matrix()
    assert set(anchors) == set(EXPECTED_CANONICAL_BLOBS)
    for source_path, (source_mode, install_mode, expected_blob) in EXPECTED_CANONICAL_BLOBS.items():
        actual_source_mode, actual_install_mode, installed_path, actual_blob = anchors[source_path]
        assert (actual_source_mode, actual_install_mode, actual_blob) == (
            source_mode,
            install_mode,
            expected_blob,
        )
        assert installed_path.startswith(("/usr/local/lib/", "/usr/local/libexec/"))
        git_mode, git_blob = _git_ls_tree(source_path)
        assert (git_mode, git_blob) == (source_mode, expected_blob)


def test_generated_host_anchor_contract_is_exact_and_public_safe() -> None:
    env = _workflow()["jobs"]["host-self-audit"]["env"]
    assert env["EXPECTED_CHECKOUT_CONFIG"] == (
        '{"schema":"skeleton.runner_controller_checkout_config.v1",'
        '"repository":"alanua/Skeleton",'
        '"checkout_path":"/home/agent/agent-dev/repos/Skeleton"}'
    )
    text = _text()
    assert "verify_regular_root_anchor \"$checkout_config\" 444" in text
    assert (
        "verify_regular_root_anchor /etc/sudoers.d/skeleton-runner-controller-privileged-gateway 440"
        in text
    )
    assert "sudoers_anchor_stat_verified=true" in text
    assert "ssh=DISABLED_NOT_AUDITED" not in text
    assert "ssh_transport=DISABLED_NOT_AUDITED" in text


def test_audit_is_read_only_and_does_not_execute_pr_head_or_privileged_gateway() -> None:
    text = _text()
    forbidden = (
        "actions/checkout",
        "git checkout",
        "git switch",
        "git reset",
        "/usr/bin/sudo",
        "privileged-gateway <",
        "workflow_call",
        "secrets.",
        "curl ",
        "wget ",
        "scp ",
        "ssh ",
        "bash -c",
        "eval ",
        "os.system",
    )
    for token in forbidden:
        assert token not in text
    assert "hash-object --no-filters -- \"$installed_path\"" in text
    assert "cat -- \"$checkout_config\"" in text


def test_summary_contains_exact_pr_head_but_no_private_paths_or_outputs() -> None:
    text = _text()
    summary_start = text.index('echo "status=${status}"')
    summary_end = text.index('} >> "${GITHUB_STEP_SUMMARY}"', summary_start)
    summary = text[summary_start:summary_end]
    assert 'echo "pr_head_sha=${{ github.event.pull_request.head.sha }}"' in summary
    assert "TARGET_CHECKOUT" not in summary
    assert "/usr/local" not in summary
    assert "/etc" not in summary
    assert "GITHUB_STEP_SUMMARY" not in summary
    assert "installed_anchor_count" in summary
    assert "sudoers_anchor_stat_verified" in summary


def test_shell_script_syntax_is_valid() -> None:
    script = _workflow()["jobs"]["host-self-audit"]["steps"][0]["run"]
    result = subprocess.run(
        ["/bin/bash", "-n"],
        input=script,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr
