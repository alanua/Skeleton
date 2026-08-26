from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import textwrap

from jsonschema import Draft202012Validator

import core.runner_controller_privileged_gateway_hardening as gateway


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 26, 10, 0, 0, tzinfo=UTC)
SHA = "9c86a7fc36a40b43852cb83b99a2a7eb21e8e708"
SSH_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAISyntheticKeyOnly runner@example"


def _write_checkout_config(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema": "skeleton.runner_controller_checkout_config.v1",
                "repository": "alanua/Skeleton",
                "checkout_path": "/home/agent/agent-dev/repos/Skeleton",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _request(**overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "schema": gateway.REQUEST_SCHEMA_ID,
        "request_id": "req-hardening",
        "idempotency_key": "idem-hardening",
        "action_id": "home_edge_01_esp_lab_stage1_signer_install_v1",
        "issued_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (NOW + timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repository": "alanua/Skeleton",
        "target": "runner-controller",
        "operator_approval": "EXACT_HEAD_HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_V2_APPROVED",
        "expected_main_sha": SHA,
        "registered_clean_main_sha": SHA,
        "github_main_sha": SHA,
        "checkout_path": "/home/agent/agent-dev/repos/Skeleton",
        "checkout_head_sha": SHA,
        "checkout_origin_main_sha": SHA,
    }
    request.update(overrides)
    return request


def _executor_report(status: str = "DONE", reason: str = "SIGNER_INSTALLATION_VERIFIED") -> str:
    receipt = {
        "status": status,
        "reason": reason,
        "expected_main_sha": SHA,
        "source_blob": "ef285000113c1254170b8924b4c3ab8d82250423",
        "installer_sha256": "a" * 64,
        "protected_copy_verified": status == "DONE",
        "installed_artifacts_verified": status == "DONE",
        "activation_executed": False,
    }
    return "RESULT: " + status + "\nReceipt:\n" + json.dumps(receipt, sort_keys=True)


def _receipt_hash(receipt: dict[str, object]) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(
            {key: value for key, value in receipt.items() if key != "receipt_hash"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _execute(
    tmp_path: Path,
    request: dict[str, object],
    runner,
    *,
    now: datetime = NOW,
    capability_path: Path | None = None,
) -> dict[str, object]:
    return gateway.execute_gateway_request(
        request,
        now=now,
        replay_ledger_path=tmp_path / "ledger.jsonl",
        registry_path=ROOT / "RUNNER_PRIVILEGED_ACTIONS.yaml",
        capability_registry_path=capability_path or ROOT / "CAPABILITY_REGISTRY.yaml",
        checkout_config_path=_write_checkout_config(tmp_path / "checkout.json"),
        runner=runner,
    )


def test_source_trust_anchors_are_fail_closed_before_runner(tmp_path: Path) -> None:
    calls = 0

    def runner(_request: object) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        raise AssertionError("runner must not execute")

    missing_capability = tmp_path / "CAPABILITY_REGISTRY.yaml"
    missing_capability.write_text("version: \"1.0.0\"\ncapabilities:\n", encoding="utf-8")
    receipt = _execute(tmp_path, _request(), runner, capability_path=missing_capability)
    assert receipt["reason"] == "CAPABILITY_REGISTRY_GATEWAY_MISSING"
    assert receipt["mutation_started"] is False
    assert receipt["mutation_performed"] is False
    assert calls == 0


def test_malformed_unserializable_request_value_blocks_without_receipt_crash(tmp_path: Path) -> None:
    calls = 0

    def runner(_request: object) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        raise AssertionError("runner must not execute")

    receipt = _execute(tmp_path, _request(request_id=object()), runner)
    assert receipt["reason"] == "REQUEST_ID_INVALID"
    assert receipt["request_hash"] is None
    assert receipt["mutation_started"] is False
    assert receipt["mutation_performed"] is False
    assert calls == 0


def test_exact_terminal_retry_returns_cached_receipt_without_second_action(tmp_path: Path) -> None:
    calls = 0

    def runner(_request: object) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        return 0, _executor_report()

    first = _execute(tmp_path, _request(), runner)
    second = _execute(tmp_path, _request(), runner, now=NOW + timedelta(days=1))
    assert first == second
    assert first["status"] == "DONE"
    assert first["mutation_started"] is True
    assert first["mutation_performed"] is True
    assert first["external_side_effects_executed"] is True
    assert calls == 1


def test_poisoned_terminal_receipt_blocks_replay_before_runner(tmp_path: Path) -> None:
    request = _request()
    request_hash = gateway.canonical_request_hash(request)
    poisoned_receipt: dict[str, object] = {
        "schema": gateway.RECEIPT_SCHEMA_ID,
        "status": "DONE",
        "reason": "SIGNER_INSTALLATION_VERIFIED",
        "action_id": request["action_id"],
        "repository": "evil/repo",
        "target": "runner-controller",
        "request_hash": request_hash,
        "mutation_started": True,
        "mutation_performed": True,
        "private_evidence_exposed": False,
        "stderr_exposed": False,
        "env_exposed": False,
        "private_paths_exposed": False,
        "external_side_effects_executed": True,
    }
    poisoned_receipt["receipt_hash"] = _receipt_hash(poisoned_receipt)
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "kind": "terminal",
                "request_hash": request_hash,
                "idempotency_key": request["idempotency_key"],
                "action_id": request["action_id"],
                "receipt": poisoned_receipt,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    calls = 0

    def runner(_request: object) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        raise AssertionError("runner must not execute")

    receipt = _execute(tmp_path, request, runner)
    assert receipt["reason"] == "REPLAY_LEDGER_CORRUPT"
    assert receipt["mutation_started"] is False
    assert calls == 0


def test_conflicting_idempotency_key_blocks_without_second_action(tmp_path: Path) -> None:
    calls = 0

    def runner(_request: object) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        return 0, _executor_report()

    first = _execute(tmp_path, _request(), runner)
    assert first["status"] == "DONE"
    conflict = _execute(tmp_path, _request(request_id="req-different"), runner)
    assert conflict["reason"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert conflict["mutation_started"] is False
    assert calls == 1


def test_reserved_without_terminal_blocks_uncertain_retry(tmp_path: Path) -> None:
    request = _request()
    request_hash = gateway.canonical_request_hash(request)
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "kind": "reservation",
                "request_hash": request_hash,
                "idempotency_key": request["idempotency_key"],
                "action_id": request["action_id"],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    calls = 0

    def runner(_request: object) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        raise AssertionError("runner must not execute")

    receipt = _execute(tmp_path, request, runner)
    assert receipt["reason"] == "PRIOR_EXECUTION_STATE_UNCERTAIN"
    assert receipt["mutation_started"] is False
    assert calls == 0


def test_nonzero_or_partial_action_is_conservatively_reported_as_mutation(tmp_path: Path) -> None:
    def runner(_request: object) -> tuple[int, str]:
        return 1, "private stderr must not escape"

    receipt = _execute(tmp_path, _request(), runner)
    assert receipt["status"] == "NEEDS_OPERATOR"
    assert receipt["reason"] == "ACTION_EXECUTOR_FAILED"
    assert receipt["mutation_started"] is True
    assert receipt["mutation_performed"] is True
    assert receipt["external_side_effects_executed"] is True
    serialized = json.dumps(receipt, sort_keys=True)
    assert "private stderr" not in serialized
    assert receipt["stderr_exposed"] is False
    assert receipt["env_exposed"] is False
    assert receipt["private_paths_exposed"] is False


def test_root_child_allows_only_exact_two_privileged_command_shapes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    staged_parent = tmp_path / "skeleton-esp-stage1-signer-abc123"
    staged_parent.mkdir()
    staged = staged_parent / "install_home_edge_esp_lab_activation_signer.sh"
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(gateway.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(gateway.subprocess, "run", fake_run)

    code, output = gateway._root_local_protected_run(
        [
            "/usr/bin/sudo",
            "-n",
            "/usr/bin/install",
            "-D",
            "-o",
            "root",
            "-g",
            "root",
            "-m",
            "0555",
            str(staged),
            gateway.ROOT_CHILD_PROTECTED_INSTALLER,
        ],
        60,
    )
    assert code == 0
    assert output == "ok"

    code, output = gateway._root_local_protected_run(
        [
            "/usr/bin/sudo",
            "-n",
            gateway.ROOT_CHILD_PROTECTED_INSTALLER,
            "--repo-root",
            "/home/agent/agent-dev/repos/Skeleton",
        ],
        120,
    )
    assert code == 0
    assert output == "ok"
    assert calls == [
        [
            "/usr/bin/install",
            "-D",
            "-o",
            "root",
            "-g",
            "root",
            "-m",
            "0555",
            str(staged),
            gateway.ROOT_CHILD_PROTECTED_INSTALLER,
        ],
        [
            gateway.ROOT_CHILD_PROTECTED_INSTALLER,
            "--repo-root",
            "/home/agent/agent-dev/repos/Skeleton",
        ],
    ]


def test_root_child_blocks_unregistered_or_mutated_privileged_command_shapes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    staged_parent = tmp_path / "skeleton-esp-stage1-signer-abc123"
    staged_parent.mkdir()
    staged = staged_parent / "install_home_edge_esp_lab_activation_signer.sh"
    monkeypatch.setattr(gateway.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        gateway.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("blocked root child argv must not execute")
        ),
    )

    blocked = [
        ["/usr/bin/sudo", "-n", "/bin/sh", "-c", "id"],
        [
            "/usr/bin/sudo",
            "-n",
            "/usr/bin/install",
            "-D",
            "-o",
            "root",
            "-g",
            "root",
            "-m",
            "04755",
            str(staged),
            gateway.ROOT_CHILD_PROTECTED_INSTALLER,
        ],
        [
            "/usr/bin/sudo",
            "-n",
            "/usr/bin/install",
            "-D",
            "-o",
            "root",
            "-g",
            "root",
            "-m",
            "0555",
            str(tmp_path / "other" / "install_home_edge_esp_lab_activation_signer.sh"),
            gateway.ROOT_CHILD_PROTECTED_INSTALLER,
        ],
        [
            "/usr/bin/sudo",
            "-n",
            gateway.ROOT_CHILD_PROTECTED_INSTALLER,
            "--repo-root",
            str(tmp_path),
        ],
    ]
    for argv in blocked:
        try:
            gateway._root_local_protected_run(argv, 60)
        except gateway.PrivilegedGatewayError as exc:
            assert exc.reason_code == "ROOT_CHILD_ACTION_NOT_ALLOWED"
        else:
            raise AssertionError(f"unexpectedly allowed root child argv: {argv!r}")


def test_public_receipt_matches_repository_schema(tmp_path: Path) -> None:
    receipt = _execute(tmp_path, _request(), lambda _request: (0, _executor_report()))
    schema = json.loads((ROOT / "schemas/runner_controller_privileged_receipt.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(receipt)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def _copy_text(repo: Path, relative: str, mode: int = 0o644) -> None:
    source = ROOT / relative
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    target.chmod(mode)


def _hardened_synthetic_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "runner@example.invalid")
    _git(repo, "config", "user.name", "Runner")
    for relative, mode in (
        ("scripts/install_runner_controller_privileged_gateway.sh", 0o755),
        ("scripts/runner_controller_privileged_gateway.py", 0o755),
        ("core/runner_controller_privileged_gateway.py", 0o644),
        ("core/runner_controller_privileged_gateway_hardening.py", 0o644),
        ("core/home_edge/esp_lab_stage1_signer_install.py", 0o644),
        ("RUNNER_PRIVILEGED_ACTIONS.yaml", 0o644),
        ("CAPABILITY_REGISTRY.yaml", 0o644),
        ("schemas/runner_controller_privileged_request.schema.json", 0o644),
        ("schemas/runner_controller_privileged_receipt.schema.json", 0o644),
        ("docs/RUNNER_CONTROLLER_PRIVILEGED_GATEWAY.md", 0o644),
    ):
        _copy_text(repo, relative, mode)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "hardened synthetic fixture")
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "origin", "HEAD:main")
    _git(repo, "fetch", "origin", "main")
    return repo, sha


def test_hardened_synthetic_bootstrap_installs_exact_trust_and_functional_forced_account_plan(tmp_path: Path) -> None:
    repo, sha = _hardened_synthetic_repo(tmp_path)
    sshd = tmp_path / "synthetic-sshd"
    sshd.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    sshd.chmod(0o755)
    dest = tmp_path / "dest"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SKELETON_GATEWAY_ALLOW_SYNTHETIC_ORIGIN": "1",
        "SKELETON_GATEWAY_HARDENED_SYNTHETIC": "1",
    }
    result = subprocess.run(
        [
            "bash",
            str(repo / "scripts/install_runner_controller_privileged_gateway.sh"),
            "--destdir",
            str(dest),
            "--repo-root",
            str(repo),
            "--expected-main-sha",
            sha,
            "--ssh-public-key",
            SSH_KEY,
            "--sshd-bin",
            str(sshd),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    install_root = dest / "usr/local/lib/skeleton/runner-controller"
    assert (install_root / "core/runner_controller_privileged_gateway_hardening.py").read_text(encoding="utf-8") == (
        repo / "core/runner_controller_privileged_gateway_hardening.py"
    ).read_text(encoding="utf-8")
    assert (install_root / "config/CAPABILITY_REGISTRY.yaml").read_text(encoding="utf-8") == (
        repo / "CAPABILITY_REGISTRY.yaml"
    ).read_text(encoding="utf-8")
    passwd_plan = (dest / "etc/passwd.d/skeleton-runner-gateway.plan").read_text(encoding="utf-8")
    assert passwd_plan.endswith(":/nonexistent:/bin/sh\n")
    authorized = (dest / "var/lib/skeleton/runner-controller/ssh/authorized_keys").read_text(encoding="utf-8")
    assert authorized.startswith(
        'command="/usr/bin/sudo -n /usr/local/libexec/skeleton/runner-controller/privileged-gateway",no-pty,no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-user-rc '
    )
    fragment = (dest / "etc/ssh/sshd_config.d/skeleton-runner-controller-privileged-gateway.conf").read_text(encoding="utf-8")
    assert "PermitRootLogin" not in fragment
    assert "PermitTTY no" in fragment
    assert "AllowTcpForwarding no" in fragment
    assert "ForceCommand /usr/bin/sudo -n /usr/local/libexec/skeleton/runner-controller/privileged-gateway" in fragment


def test_installer_live_account_is_locked_valid_shell_and_no_generic_root_shell() -> None:
    text = (ROOT / "scripts/install_runner_controller_privileged_gateway.sh").read_text(encoding="utf-8")
    assert 'SSH_SHELL="/bin/sh"' in text
    assert 'passwd -l "$SSH_USER" >/dev/null 2>&1 || block "ssh-account-lock-failed"' in text
    assert 'useradd --system --home-dir /nonexistent --shell "$SSH_SHELL" --no-create-home "$SSH_USER"' in text
    assert 'usermod --home /nonexistent --shell "$SSH_SHELL" "$SSH_USER"' in text
    assert "PermitRootLogin" not in text
    assert "systemctl reload" not in text
    assert "sudo bash" not in text
    assert "bash -c" not in text
