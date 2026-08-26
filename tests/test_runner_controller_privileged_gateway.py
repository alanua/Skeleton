from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

from jsonschema import Draft202012Validator
import pytest

import core.runner_controller_privileged_gateway as gateway
import core.runner_repository_maintenance_executor as maintenance


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 25, 12, 0, 0, tzinfo=UTC)
SHA = "8e049eb631f63d81ab932eac6ab0cf3d3d5a5949"
SSH_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAISyntheticKeyOnly runner@example"


REQUEST_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": gateway.REQUEST_SCHEMA_ID,
    "type": "object",
    "additionalProperties": False,
    "required": list(gateway.REQUEST_FIELDS),
    "properties": {
        "schema": {"const": gateway.REQUEST_SCHEMA_ID},
        "request_id": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$"},
        "idempotency_key": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$"},
        "action_id": {"const": maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID},
        "issued_at": {"type": "string"},
        "expires_at": {"type": "string"},
        "repository": {"const": "alanua/Skeleton"},
        "target": {"const": "runner-controller"},
        "operator_approval": {"const": maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_OPERATOR_APPROVAL},
        "expected_main_sha": {"type": "string", "pattern": r"^[0-9a-f]{40}$"},
        "registered_clean_main_sha": {"type": "string", "pattern": r"^[0-9a-f]{40}$"},
        "github_main_sha": {"type": "string", "pattern": r"^[0-9a-f]{40}$"},
        "checkout_path": {"type": "string"},
        "checkout_head_sha": {"type": "string", "pattern": r"^[0-9a-f]{40}$"},
        "checkout_origin_main_sha": {"type": "string", "pattern": r"^[0-9a-f]{40}$"},
    },
}
RECEIPT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": gateway.RECEIPT_SCHEMA_ID,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema",
        "status",
        "reason",
        "action_id",
        "repository",
        "target",
        "request_hash",
        "private_evidence_exposed",
        "stderr_exposed",
        "env_exposed",
        "private_paths_exposed",
        "external_side_effects_executed",
        "receipt_hash",
    ],
    "properties": {
        "schema": {"const": gateway.RECEIPT_SCHEMA_ID},
        "status": {"enum": ["DONE", "NEEDS_OPERATOR"]},
        "reason": {"type": "string"},
        "action_id": {"type": "string"},
        "repository": {"const": "alanua/Skeleton"},
        "target": {"const": "runner-controller"},
        "request_hash": {"type": ["string", "null"]},
        "expected_main_sha": {"type": "string"},
        "source_blob": {"type": "string"},
        "installer_sha256": {"type": "string"},
        "protected_copy_verified": {"type": "boolean"},
        "installed_artifacts_verified": {"type": "boolean"},
        "activation_executed": {"type": "boolean"},
        "private_evidence_exposed": {"const": False},
        "stderr_exposed": {"const": False},
        "env_exposed": {"const": False},
        "private_paths_exposed": {"const": False},
        "external_side_effects_executed": {"type": "boolean"},
        "receipt_hash": {"type": "string"},
    },
}


def _request(**overrides: object) -> dict[str, object]:
    request = gateway.build_gateway_request(
        request_id="req-esp-stage1-signer",
        idempotency_key="idem-esp-stage1-signer",
        expected_main_sha=SHA,
        registered_clean_main_sha=SHA,
        github_main_sha=SHA,
        checkout_path=Path("/home/agent/agent-dev/repos/Skeleton"),
        checkout_head_sha=SHA,
        checkout_origin_main_sha=SHA,
        issued_at=NOW,
        expires_at=NOW + timedelta(seconds=120),
    )
    request.update(overrides)
    return request


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def _write(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def _action_registry() -> str:
    return textwrap.dedent(
        f"""\
        schema: skeleton.runner_privileged_actions.v1
        actions:
          - action_id: {maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID}
            handler: home_edge_esp_lab_stage1_signer_install
            repository: alanua/Skeleton
            target: runner-controller
            operator_approval: {maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_OPERATOR_APPROVAL}
            source_path: {maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_SOURCE_PATH}
            source_blob: {maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_BLOB}
            source_mode: "{maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_MODE}"
            trusted_source_ancestor_sha: {maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_TRUSTED_SOURCE_ANCESTOR_SHA}
            destination: {maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PROTECTED_INSTALLER}
            installer_argv:
              - {maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PROTECTED_INSTALLER}
              - --repo-root
              - "{{checkout_path}}"
            post_audit_artifacts:
              - path: /usr/local/libexec/skeleton/home-edge/esp-lab-stage1/signer
                content_hash: {maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_WRAPPER_BLOB}
                mode: "0555"
              - path: /usr/local/lib/skeleton/home-edge/esp-lab-stage1/signer_payload.py
                content_hash: {maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PAYLOAD_BLOB}
                mode: "0555"
              - path: /usr/local/lib/skeleton/home-edge/esp-lab-stage1/install_home_edge_esp_lab.sh
                content_hash: {maintenance.HOME_EDGE_ESP_LAB_STAGE1_INSTALLER_BLOB}
                mode: "0444"
              - path: /etc/sudoers.d/skeleton-home-edge-esp-lab-stage1-signer
                content_hash: {maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_SUDOERS_SHA256}
                mode: "0440"
        """
    )


def _signer_module() -> str:
    return textwrap.dedent(
        f"""\
        from __future__ import annotations
        import json
        import re
        from pathlib import Path
        HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID = {maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID!r}
        HOME_EDGE_ESP_LAB_STAGE1_SIGNER_APPROVED_MAIN_SHA = {maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_APPROVED_MAIN_SHA!r}
        HOME_EDGE_ESP_LAB_STAGE1_SIGNER_TRUSTED_SOURCE_ANCESTOR_SHA = {maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_TRUSTED_SOURCE_ANCESTOR_SHA!r}
        HOME_EDGE_ESP_LAB_STAGE1_SIGNER_SOURCE_PATH = {maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_SOURCE_PATH!r}
        HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_BLOB = {maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_BLOB!r}
        HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_MODE = {maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_MODE!r}
        HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PAYLOAD_BLOB = {maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PAYLOAD_BLOB!r}
        HOME_EDGE_ESP_LAB_STAGE1_SIGNER_WRAPPER_BLOB = {maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_WRAPPER_BLOB!r}
        HOME_EDGE_ESP_LAB_STAGE1_INSTALLER_BLOB = {maintenance.HOME_EDGE_ESP_LAB_STAGE1_INSTALLER_BLOB!r}
        HOME_EDGE_ESP_LAB_STAGE1_SIGNER_SUDOERS_SHA256 = {maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_SUDOERS_SHA256!r}
        HOME_EDGE_ESP_LAB_STAGE1_SIGNER_OPERATOR_APPROVAL = {maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_OPERATOR_APPROVAL!r}
        HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PROTECTED_INSTALLER = Path({str(maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PROTECTED_INSTALLER)!r})
        def _protected_receipt(status, reason, *, expected_main_sha=None, installer_sha256=None, artifacts_ok=False, protected_copy_verified=None):
            receipt = {{"maintenance_task_id": HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID, "status": status, "reason": re.sub(r"[^A-Z0-9_]+", "_", reason.upper()).strip("_") or "BLOCKED", "repository": "alanua/Skeleton", "expected_main_sha": expected_main_sha or HOME_EDGE_ESP_LAB_STAGE1_SIGNER_APPROVED_MAIN_SHA, "target": "runner-controller", "source_blob": HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_BLOB, "protected_copy_verified": status == "DONE" if protected_copy_verified is None else protected_copy_verified, "installed_artifacts_verified": artifacts_ok, "activation_executed": False, "private_evidence_exposed": False}}
            if installer_sha256 is not None:
                receipt["installer_sha256"] = installer_sha256
            return receipt
        def _protected_result(status, receipt):
            return "RESULT: " + status + "\\nReceipt:\\n" + json.dumps(receipt, indent=2, sort_keys=True)
        def execute_home_edge_esp_lab_stage1_signer_install(**kwargs):
            return 0, _protected_result("DONE", _protected_receipt("DONE", "SIGNER_INSTALLATION_VERIFIED", expected_main_sha=kwargs.get("expected_main_sha"), installer_sha256="a" * 64, artifacts_ok=True))
        """
    )


def _make_synthetic_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "runner@example.invalid")
    _git(repo, "config", "user.name", "Runner")
    _write(repo / "scripts/install_runner_controller_privileged_gateway.sh", (ROOT / "scripts/install_runner_controller_privileged_gateway.sh").read_text(encoding="utf-8"), 0o755)
    _write(repo / "scripts/runner_controller_privileged_gateway.py", (ROOT / "scripts/runner_controller_privileged_gateway.py").read_text(encoding="utf-8"), 0o755)
    _write(repo / "core/runner_controller_privileged_gateway.py", (ROOT / "core/runner_controller_privileged_gateway.py").read_text(encoding="utf-8"))
    _write(repo / "core/home_edge/esp_lab_stage1_signer_install.py", _signer_module())
    _write(repo / "RUNNER_PRIVILEGED_ACTIONS.yaml", _action_registry())
    _write(repo / "schemas/runner_controller_privileged_request.schema.json", json.dumps(REQUEST_SCHEMA, indent=2, sort_keys=True))
    _write(repo / "schemas/runner_controller_privileged_receipt.schema.json", json.dumps(RECEIPT_SCHEMA, indent=2, sort_keys=True))
    _write(repo / "docs/RUNNER_CONTROLLER_PRIVILEGED_GATEWAY.md", (ROOT / "docs/RUNNER_CONTROLLER_PRIVILEGED_GATEWAY.md").read_text(encoding="utf-8"))
    _write(repo / maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_SOURCE_PATH, "#!/usr/bin/env bash\nexit 0\n", 0o755)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "synthetic exact object fixture")
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(tmp_path, "init", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "origin", "HEAD:main")
    _git(repo, "fetch", "origin", "main")
    return repo, remote, sha


def _run_installer(repo: Path, destdir: Path, sha: str, *extra: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    child_env = {"PATH": os.environ.get("PATH", ""), "SKELETON_GATEWAY_ALLOW_SYNTHETIC_ORIGIN": "1"}
    if env:
        child_env.update(env)
    return subprocess.run(
        [
            "bash",
            str(repo / "scripts/install_runner_controller_privileged_gateway.sh"),
            "--destdir",
            str(destdir),
            "--repo-root",
            str(repo),
            "--expected-main-sha",
            sha,
            *extra,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env,
        check=False,
    )


def test_request_and_receipt_schemas_are_exact_and_gateway_outputs_validate() -> None:
    request = _request()
    Draft202012Validator(REQUEST_SCHEMA).validate(request)
    receipt = gateway.execute_gateway_request(
        request,
        now=NOW,
        runner=lambda _request, _action: (
            0,
            maintenance._protected_result(
                "DONE",
                maintenance._protected_receipt(
                    "DONE",
                    "SIGNER_INSTALLATION_VERIFIED",
                    expected_main_sha=SHA,
                    installer_sha256="a" * 64,
                    artifacts_ok=True,
                ),
            ),
        ),
    )
    assert receipt["status"] == "DONE"
    assert receipt["private_evidence_exposed"] is False
    Draft202012Validator(RECEIPT_SCHEMA).validate(receipt)


@pytest.mark.parametrize(
    ("override", "reason"),
    (
        ({"extra": "field"}, "REQUEST_FIELD_SET_MISMATCH"),
        ({"action_id": "shell"}, "ACTION_NOT_REGISTERED"),
        ({"checkout_path": "/tmp/issue-controlled"}, "CHECKOUT_PATH_INVALID"),
        ({"expected_main_sha": "0" * 40}, "REQUEST_SHA_MISMATCH"),
        ({"operator_approval": "wrong"}, "OPERATOR_APPROVAL_MISMATCH"),
        (
            {
                "issued_at": (NOW - timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "expires_at": (NOW - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            "REQUEST_EXPIRED_OR_NOT_YET_VALID",
        ),
    ),
)
def test_bounded_input_blocks_before_runner(override: dict[str, object], reason: str) -> None:
    calls = 0

    def runner(_request: object, _action: object) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        raise AssertionError("runner must not be reached")

    receipt = gateway.execute_gateway_request(_request(**override), now=NOW, runner=runner)
    assert receipt["reason"] == reason
    assert calls == 0


def test_replay_blocks_same_canonical_request_before_runner() -> None:
    seen: set[str] = set()
    calls = 0

    def runner(_request: object, _action: object) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        return 0, maintenance._protected_result("NEEDS_OPERATOR", maintenance._protected_receipt("NEEDS_OPERATOR", "SYNTHETIC"))

    first = gateway.execute_gateway_request(_request(), now=NOW, seen_request_hashes=seen, runner=runner)
    second = gateway.execute_gateway_request(_request(), now=NOW, seen_request_hashes=seen, runner=runner)
    assert first["reason"] == "SYNTHETIC"
    assert second["reason"] == "REQUEST_REPLAY"
    assert calls == 1


def test_local_sudo_and_ssh_deliver_identical_bytes_and_no_extra_gateway_argv() -> None:
    current = gateway._utc_now()
    request = _request(
        issued_at=current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires_at=(current + timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    assert gateway.LocalSudoGatewayTransport().canonical_request(request) == gateway.SshForcedCommandGatewayTransport().canonical_request(request)
    assert gateway.LOCAL_SUDO_GATEWAY_ARGV == ("/usr/bin/sudo", "-n", "/usr/local/libexec/skeleton/runner-controller/privileged-gateway")
    assert gateway.FORCED_COMMAND_ARGV == gateway.LOCAL_SUDO_GATEWAY_ARGV


def test_ssh_restrictions_and_root_login_prohibition_are_deterministic() -> None:
    line = gateway.SshForcedCommandGatewayTransport().authorized_keys_line(SSH_KEY)
    assert line.startswith(
        'command="/usr/bin/sudo -n /usr/local/libexec/skeleton/runner-controller/privileged-gateway",'
        "no-pty,no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-user-rc "
    )
    fragment = gateway.deterministic_sshd_config_fragment()
    assert "PermitRootLogin" not in fragment
    assert "Match User skeleton-runner-gateway" in fragment
    assert "AuthorizedKeysFile /var/lib/skeleton/runner-controller/ssh/authorized_keys" in fragment
    assert "PermitTTY no" in fragment
    assert "AllowTcpForwarding no" in fragment
    assert "AllowAgentForwarding no" in fragment
    assert "PermitUserRC no" in fragment
    assert "ForceCommand /usr/bin/sudo -n /usr/local/libexec/skeleton/runner-controller/privileged-gateway" in fragment


def test_action_registry_first_action_preserves_exact_existing_esp_signer_contract(tmp_path: Path) -> None:
    registry_path = ROOT / "RUNNER_PRIVILEGED_ACTIONS.yaml"
    if not registry_path.exists():
        registry_path = _temp_registry(tmp_path)
    actions = gateway.load_action_registry(path=registry_path)
    action = actions[maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID]
    assert list(actions) == [maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID]
    assert action.source_blob == maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_BLOB
    assert action.installer_argv == (
        str(maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PROTECTED_INSTALLER),
        "--repo-root",
        "{checkout_path}",
    )


def _temp_registry(root: Path) -> Path:
    path = root / "RUNNER_PRIVILEGED_ACTIONS.yaml"
    path.write_text(_action_registry(), encoding="utf-8")
    return path


def test_destdir_exact_object_bootstrap_uses_committed_bytes_and_dirty_tamper_blocks(tmp_path: Path) -> None:
    repo, _remote, sha = _make_synthetic_repo(tmp_path)
    committed_gateway = (repo / "core/runner_controller_privileged_gateway.py").read_text(encoding="utf-8")
    result = _run_installer(repo, tmp_path / "dest", sha)
    assert result.returncode == 0, result.stderr
    installed_gateway = tmp_path / "dest/usr/local/lib/skeleton/runner-controller/core/runner_controller_privileged_gateway.py"
    assert installed_gateway.read_text(encoding="utf-8") == committed_gateway
    assert "ssh=DISABLED_NOT_CONFIGURED" in result.stdout
    assert not (tmp_path / "dest/etc/ssh/sshd_config.d/skeleton-runner-controller-privileged-gateway.conf").exists()
    assert not (tmp_path / "dest/var/lib/skeleton/runner-controller/ssh/authorized_keys").exists()

    (repo / "core/runner_controller_privileged_gateway.py").write_text("# tampered\n", encoding="utf-8")
    dirty = _run_installer(repo, tmp_path / "dirty", sha)
    assert dirty.returncode == 2
    assert "worktree-not-clean" in dirty.stderr
    assert not (tmp_path / "dirty/usr/local/libexec/skeleton/runner-controller/privileged-gateway").exists()


def test_wrong_sha_wrong_origin_stale_origin_fresh_remote_and_installer_mismatch_block_before_install(tmp_path: Path) -> None:
    repo, remote, sha = _make_synthetic_repo(tmp_path)
    wrong_sha = _run_installer(repo, tmp_path / "wrong-sha", "0" * 40)
    assert wrong_sha.returncode == 2
    assert "head-sha-mismatch" in wrong_sha.stderr

    _git(repo, "remote", "set-url", "origin", "https://example.invalid/not-skeleton.git")
    wrong_origin = _run_installer(repo, tmp_path / "wrong-origin", sha, env={"SKELETON_GATEWAY_ALLOW_SYNTHETIC_ORIGIN": "0"})
    assert wrong_origin.returncode == 2
    assert "origin-mismatch" in wrong_origin.stderr
    _git(repo, "remote", "set-url", "origin", str(remote))

    _write(repo / "README.md", "advance remote\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "advance remote")
    advanced_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "push", "origin", "HEAD:main")
    _git(repo, "reset", "--hard", sha)
    stale = _run_installer(repo, tmp_path / "stale", sha)
    assert stale.returncode == 2
    assert "origin-main-sha-mismatch" in stale.stderr
    _git(repo, "fetch", "origin", "main")
    fresh_mismatch = _run_installer(repo, tmp_path / "fresh-mismatch", sha)
    assert fresh_mismatch.returncode == 2
    assert "origin-main-sha-mismatch" in fresh_mismatch.stderr or "fresh-remote-main-mismatch" in fresh_mismatch.stderr
    _git(repo, "reset", "--hard", advanced_sha)

    script = repo / "scripts/install_runner_controller_privileged_gateway.sh"
    script.write_text(script.read_text(encoding="utf-8") + "\n# tamper\n", encoding="utf-8")
    script.chmod(0o755)
    _git(repo, "add", str(script.relative_to(repo)))
    _git(repo, "commit", "-m", "tamper installer")
    tampered_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "push", "origin", "HEAD:main")
    script.write_text((ROOT / "scripts/install_runner_controller_privileged_gateway.sh").read_text(encoding="utf-8"), encoding="utf-8")
    script.chmod(0o755)
    _git(repo, "update-index", "--assume-unchanged", str(script.relative_to(repo)))
    installer_mismatch = _run_installer(repo, tmp_path / "installer-mismatch", tampered_head)
    _git(repo, "update-index", "--no-assume-unchanged", str(script.relative_to(repo)), check=False)
    assert installer_mismatch.returncode == 2
    assert "installer-blob-mismatch" in installer_mismatch.stderr


def test_key_path_installs_exact_account_key_sudoers_match_and_no_root_policy(tmp_path: Path) -> None:
    repo, _remote, sha = _make_synthetic_repo(tmp_path)
    sshd = tmp_path / "synthetic-sshd"
    sshd.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    sshd.chmod(0o755)
    dest = tmp_path / "dest"
    result = _run_installer(repo, dest, sha, "--ssh-public-key", SSH_KEY, "--sshd-bin", str(sshd))
    assert result.returncode == 0, result.stderr
    assert "ssh=READY" in result.stdout
    assert (dest / "etc/passwd.d/skeleton-runner-gateway.plan").read_text(encoding="utf-8").endswith(":/nonexistent:/usr/sbin/nologin\n")
    assert (dest / "var/lib/skeleton/runner-controller/ssh/authorized_keys").read_text(encoding="utf-8") == (
        f'command="/usr/bin/sudo -n /usr/local/libexec/skeleton/runner-controller/privileged-gateway",no-pty,no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-user-rc {SSH_KEY}\n'
    )
    assert (dest / "etc/sudoers.d/skeleton-runner-controller-privileged-gateway").read_text(encoding="utf-8") == (
        'agent ALL=(root) NOPASSWD: /usr/local/libexec/skeleton/runner-controller/privileged-gateway ""\n'
    )
    assert (dest / "etc/sudoers.d/skeleton-runner-gateway").read_text(encoding="utf-8") == (
        'skeleton-runner-gateway ALL=(root) NOPASSWD: /usr/local/libexec/skeleton/runner-controller/privileged-gateway ""\n'
    )
    fragment = (dest / "etc/ssh/sshd_config.d/skeleton-runner-controller-privileged-gateway.conf").read_text(encoding="utf-8")
    assert "PermitRootLogin" not in fragment
    assert "AuthorizedKeysFile /var/lib/skeleton/runner-controller/ssh/authorized_keys" in fragment
    assert "PermitOpen none" in fragment
    assert "PermitListen none" in fragment
    assert "ForceCommand /usr/bin/sudo -n /usr/local/libexec/skeleton/runner-controller/privileged-gateway" in fragment


def test_no_extra_argv_allowed_in_generated_sudo_policy(tmp_path: Path) -> None:
    repo, _remote, sha = _make_synthetic_repo(tmp_path)
    result = _run_installer(repo, tmp_path / "dest", sha)
    assert result.returncode == 0, result.stderr
    policy = (tmp_path / "dest/etc/sudoers.d/skeleton-runner-controller-privileged-gateway").read_text(encoding="utf-8")
    assert 'privileged-gateway ""' in policy
    assert "--forced-command" not in policy


def test_synthetic_sshd_success_and_failure_rolls_back_bad_fragment(tmp_path: Path) -> None:
    repo, _remote, sha = _make_synthetic_repo(tmp_path)
    bad_sshd = tmp_path / "bad-sshd"
    bad_sshd.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    bad_sshd.chmod(0o755)
    dest = tmp_path / "dest"
    result = _run_installer(repo, dest, sha, "--ssh-public-key", SSH_KEY, "--sshd-bin", str(bad_sshd))
    assert result.returncode == 2
    assert "sshd-validation-failed" in result.stderr
    assert not (dest / "etc/ssh/sshd_config.d/skeleton-runner-controller-privileged-gateway.conf").exists()


def test_installed_tree_valid_signer_request_and_import_closure_remain_green(tmp_path: Path) -> None:
    repo, _remote, sha = _make_synthetic_repo(tmp_path)
    install = _run_installer(repo, tmp_path / "dest", sha)
    assert install.returncode == 0, install.stderr
    install_root = tmp_path / "dest/usr/local/lib/skeleton/runner-controller"
    code = f"""
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
sys.path.insert(0, {str(install_root)!r})
import core.home_edge.esp_lab_stage1_signer_install as signer
import core.runner_controller_privileged_gateway as gateway
now = datetime(2026, 8, 25, 12, 0, 0, tzinfo=UTC)
sha = {SHA!r}
request = gateway.build_gateway_request(request_id="req-installed-valid", idempotency_key="idem-installed-valid", expected_main_sha=sha, registered_clean_main_sha=sha, github_main_sha=sha, checkout_path=Path("/home/agent/agent-dev/repos/Skeleton"), checkout_head_sha=sha, checkout_origin_main_sha=sha, issued_at=now, expires_at=now + timedelta(seconds=120))
receipt = gateway.execute_gateway_request(request, now=now, runner=lambda request, action: signer.execute_home_edge_esp_lab_stage1_signer_install(expected_main_sha=sha))
print(json.dumps(receipt, sort_keys=True))
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=str(tmp_path), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "DONE"
    installed = {path.relative_to(install_root).as_posix() for path in install_root.rglob("*.py")}
    assert installed == {
        "core/__init__.py",
        "core/home_edge/__init__.py",
        "core/home_edge/esp_lab_stage1_signer_install.py",
        "core/runner_controller_privileged_gateway.py",
    }
    imported: set[str] = set()
    for relative in installed:
        tree = ast.parse((install_root / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)
    assert not any(name.startswith("core.runner_executor") for name in imported)


def test_bash_n_and_py_compile_inputs() -> None:
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts/install_runner_controller_privileged_gateway.sh")], check=False).returncode == 0
    assert shutil.which("ssh-keygen") is None or "PRIVATE KEY" not in (ROOT / "scripts/install_runner_controller_privileged_gateway.sh").read_text(encoding="utf-8")
