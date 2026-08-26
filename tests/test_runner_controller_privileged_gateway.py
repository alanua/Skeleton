from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys

from jsonschema import Draft202012Validator
import pytest
import yaml

import core.runner_controller_privileged_gateway as gateway
import core.runner_repository_maintenance_executor as maintenance


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 25, 12, 0, 0, tzinfo=UTC)
SHA = "8e049eb631f63d81ab932eac6ab0cf3d3d5a5949"


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


def _load_json(path: str) -> dict[str, object]:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_request_and_receipt_schemas_are_exact_and_gateway_outputs_validate() -> None:
    request = _request()
    Draft202012Validator(_load_json("schemas/runner_controller_privileged_request.schema.json")).validate(request)

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
    assert receipt["stderr_exposed"] is False
    assert receipt["env_exposed"] is False
    assert receipt["private_paths_exposed"] is False
    Draft202012Validator(_load_json("schemas/runner_controller_privileged_receipt.schema.json")).validate(receipt)


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
def test_bounded_input_unknown_fields_expiry_and_sha_mismatch_block_before_runner(
    override: dict[str, object],
    reason: str,
) -> None:
    calls = 0

    def runner(_request: object, _action: object) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        raise AssertionError("runner must not be reached")

    receipt = gateway.execute_gateway_request(_request(**override), now=NOW, runner=runner)

    assert receipt["status"] == "NEEDS_OPERATOR"
    assert receipt["reason"] == reason
    assert calls == 0


def test_replay_blocks_same_canonical_request_before_runner() -> None:
    seen: set[str] = set()
    request = _request()
    calls = 0

    def runner(_request: object, _action: object) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        return 0, maintenance._protected_result(
            "NEEDS_OPERATOR",
            maintenance._protected_receipt("NEEDS_OPERATOR", "SYNTHETIC"),
        )

    first = gateway.execute_gateway_request(request, now=NOW, seen_request_hashes=seen, runner=runner)
    second = gateway.execute_gateway_request(request, now=NOW, seen_request_hashes=seen, runner=runner)

    assert first["reason"] == "SYNTHETIC"
    assert second["reason"] == "REQUEST_REPLAY"
    assert calls == 1


def test_persistent_replay_ledger_blocks_request_and_idempotency_reuse_before_runner(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    calls = 0

    def runner(_request: object, _action: object) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        return 0, maintenance._protected_result(
            "NEEDS_OPERATOR",
            maintenance._protected_receipt("NEEDS_OPERATOR", "SYNTHETIC"),
        )

    request = _request()
    first = gateway.execute_gateway_request(
        request,
        now=NOW,
        replay_ledger_path=ledger,
        runner=runner,
    )
    second = gateway.execute_gateway_request(
        request,
        now=NOW,
        replay_ledger_path=ledger,
        runner=runner,
    )
    third = gateway.execute_gateway_request(
        _request(request_id="req-different-same-idem"),
        now=NOW,
        replay_ledger_path=ledger,
        runner=runner,
    )

    assert first["reason"] == "SYNTHETIC"
    assert second["reason"] == "REQUEST_REPLAY"
    assert third["reason"] == "IDEMPOTENCY_KEY_REPLAY"
    assert calls == 1
    assert ledger.read_text(encoding="utf-8").count("\n") == 1


def test_local_sudo_and_ssh_forced_command_share_identical_canonical_semantics() -> None:
    current = gateway._utc_now()
    request = _request(
        issued_at=current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires_at=(current + timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    local_payload = gateway.LocalSudoGatewayTransport().canonical_request(request)
    ssh_payload = gateway.SshForcedCommandGatewayTransport().canonical_request(request)

    assert local_payload == ssh_payload
    assert json.loads(local_payload) == request
    assert gateway.LocalSudoGatewayTransport().argv == gateway.LOCAL_SUDO_GATEWAY_ARGV


def test_ssh_restrictions_and_root_login_prohibition_are_deterministic() -> None:
    line = gateway.SshForcedCommandGatewayTransport().authorized_keys_line(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAISyntheticKeyOnly runner@example"
    )
    assert line.startswith(
        'command="/usr/local/libexec/skeleton/runner-controller/privileged-gateway --forced-command",'
        "no-pty,no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-user-rc "
    )

    fragment = gateway.deterministic_sshd_config_fragment()
    assert "PermitRootLogin" not in fragment
    assert fragment.startswith("Match User skeleton-runner-gateway\n")
    assert "PermitTTY no" in fragment
    assert "AllowTcpForwarding no" in fragment
    assert "AllowAgentForwarding no" in fragment
    assert "ForceCommand /usr/local/libexec/skeleton/runner-controller/privileged-gateway --forced-command" in fragment


def test_action_registry_first_action_preserves_exact_existing_esp_signer_contract() -> None:
    actions = gateway.load_action_registry()
    action = actions[maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID]

    assert list(actions) == [maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID]
    assert action.source_blob == maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_BLOB
    assert action.source_path == maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_SOURCE_PATH
    assert action.source_mode == maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_MODE
    assert action.operator_approval == maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_OPERATOR_APPROVAL
    assert action.installer_argv == (
        str(maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PROTECTED_INSTALLER),
        "--repo-root",
        "{checkout_path}",
    )
    assert action.post_audit_artifacts == (
        (
            "/usr/local/libexec/skeleton/home-edge/esp-lab-stage1/signer",
            maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_WRAPPER_BLOB,
            0o555,
        ),
        (
            "/usr/local/lib/skeleton/home-edge/esp-lab-stage1/signer_payload.py",
            maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_PAYLOAD_BLOB,
            0o555,
        ),
        (
            "/usr/local/lib/skeleton/home-edge/esp-lab-stage1/install_home_edge_esp_lab.sh",
            maintenance.HOME_EDGE_ESP_LAB_STAGE1_INSTALLER_BLOB,
            0o444,
        ),
        (
            "/etc/sudoers.d/skeleton-home-edge-esp-lab-stage1-signer",
            maintenance.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_SUDOERS_SHA256,
            0o440,
        ),
    )


def test_action_registry_drift_blocks_before_root_runner(tmp_path: Path) -> None:
    registry = yaml.safe_load((ROOT / "RUNNER_PRIVILEGED_ACTIONS.yaml").read_text(encoding="utf-8"))
    registry["actions"][0]["source_blob"] = "0" * 40
    path = tmp_path / "RUNNER_PRIVILEGED_ACTIONS.yaml"
    path.write_text(yaml.safe_dump(registry), encoding="utf-8")
    calls = 0

    def runner(_request: object, _action: object) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        return 0, ""

    receipt = gateway.execute_gateway_request(_request(), now=NOW, registry_path=path, runner=runner)

    assert receipt["reason"] == "INITIAL_ESP_SIGNER_ACTION_DRIFT"
    assert calls == 0


def test_unapproved_protected_capability_metadata_blocks_before_root_runner(tmp_path: Path) -> None:
    registry = {
        "version": "1.0.0",
        "capabilities": {
            "runner_controller_privileged_gateway": {
                "status": "available",
                "module": "core/runner_controller_privileged_gateway.py",
                "live_runtime_execution": True,
                "protected": False,
                "requires": [
                    "core/runner_controller_privileged_gateway.py",
                    "scripts/runner_controller_privileged_gateway.py",
                    "scripts/install_runner_controller_privileged_gateway.sh",
                    "RUNNER_PRIVILEGED_ACTIONS.yaml",
                    "schemas/runner_controller_privileged_request.schema.json",
                    "schemas/runner_controller_privileged_receipt.schema.json",
                    "docs/RUNNER_CONTROLLER_PRIVILEGED_GATEWAY.md",
                ],
            },
        },
    }
    path = tmp_path / "CAPABILITY_REGISTRY.yaml"
    path.write_text(yaml.safe_dump(registry), encoding="utf-8")
    calls = 0

    def runner(_request: object, _action: object) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        return 0, ""

    receipt = gateway.execute_gateway_request(
        _request(),
        now=NOW,
        capability_registry_path=path,
        runner=runner,
    )

    assert receipt["reason"] == "CAPABILITY_REGISTRY_GATEWAY_UNAPPROVED"
    assert calls == 0


def test_missing_protected_capability_metadata_blocks_before_root_runner(tmp_path: Path) -> None:
    registry = {"version": "1.0.0", "capabilities": {"other": {"status": "available"}}}
    path = tmp_path / "CAPABILITY_REGISTRY.yaml"
    path.write_text(yaml.safe_dump(registry), encoding="utf-8")
    calls = 0

    def runner(_request: object, _action: object) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        return 0, ""

    receipt = gateway.execute_gateway_request(
        _request(),
        now=NOW,
        capability_registry_path=path,
        runner=runner,
    )

    assert receipt["reason"] == "CAPABILITY_REGISTRY_GATEWAY_MISSING"
    assert calls == 0


def test_partial_mutation_receipt_reports_external_side_effects_without_done() -> None:
    receipt = gateway.execute_gateway_request(
        _request(),
        now=NOW,
        runner=lambda _request, _action: (
            0,
            maintenance._protected_result(
                "NEEDS_OPERATOR",
                maintenance._protected_receipt(
                    "NEEDS_OPERATOR",
                    "SIGNER_INSTALLER_FAILED",
                    protected_copy_verified=True,
                    installer_sha256="b" * 64,
                ),
            ),
        ),
    )

    assert receipt["status"] == "NEEDS_OPERATOR"
    assert receipt["reason"] == "SIGNER_INSTALLER_FAILED"
    assert receipt["protected_copy_verified"] is True
    assert receipt["external_side_effects_executed"] is True


def test_public_receipts_do_not_expose_stderr_env_keys_or_private_paths() -> None:
    receipt = gateway.execute_gateway_request(
        _request(),
        now=NOW,
        runner=lambda _request, _action: (0, "stderr=/etc/skeleton/private-key\nSECRET=abc\n"),
    )
    serialized = json.dumps(receipt, sort_keys=True)
    assert "stderr" not in serialized.lower().replace('"stderr_exposed"', "")
    assert "/etc/skeleton" not in serialized
    assert "SECRET" not in serialized
    assert "private-key" not in serialized


def test_installer_bash_n_and_synthetic_isolated_root(tmp_path: Path) -> None:
    script = ROOT / "scripts/install_runner_controller_privileged_gateway.sh"
    assert subprocess.run(["bash", "-n", str(script)], check=False).returncode == 0

    result = subprocess.run(
        ["bash", str(script), "--destdir", str(tmp_path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0
    assert (tmp_path / "usr/local/libexec/skeleton/runner-controller/privileged-gateway").is_file()
    sudoers = tmp_path / "etc/sudoers.d/skeleton-runner-controller-privileged-gateway"
    sshd = tmp_path / "etc/ssh/sshd_config.d/skeleton-runner-controller-privileged-gateway.conf"
    assert sudoers.read_text(encoding="utf-8") == (
        'agent ALL=(root) NOPASSWD: /usr/local/libexec/skeleton/runner-controller/privileged-gateway ""\n'
    )
    assert "PermitRootLogin" not in sshd.read_text(encoding="utf-8")
    assert (
        tmp_path
        / "usr/local/lib/skeleton/runner-controller/core/runner_controller_privileged_gateway.py"
    ).is_file()
    assert (
        tmp_path
        / "usr/local/lib/skeleton/runner-controller/core/home_edge/esp_lab_stage1_signer_install.py"
    ).is_file()
    assert not (
        tmp_path
        / "usr/local/lib/skeleton/runner-controller/core/runner_repository_maintenance_executor.py"
    ).exists()
    assert (
        tmp_path / "usr/local/lib/skeleton/runner-controller/config/RUNNER_PRIVILEGED_ACTIONS.yaml"
    ).is_file()


def test_installed_tree_smoke_from_empty_cwd_and_env(tmp_path: Path) -> None:
    script = ROOT / "scripts/install_runner_controller_privileged_gateway.sh"
    install = subprocess.run(
        ["bash", str(script), "--destdir", str(tmp_path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert install.returncode == 0

    gateway_script = (
        tmp_path / "usr/local/libexec/skeleton/runner-controller/privileged-gateway"
    )
    result = subprocess.run(
        [sys.executable, str(gateway_script)],
        input=b"{}",
        cwd=str(tmp_path),
        env={"PATH": os.environ.get("PATH", "")},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0
    receipt = json.loads(result.stdout.decode("utf-8"))
    assert receipt["status"] == "NEEDS_OPERATOR"
    assert receipt["reason"] == "REQUEST_FIELD_SET_MISMATCH"
    assert result.stderr == b""


def test_installed_gateway_valid_request_reaches_synthetic_action_from_empty_cwd(
    tmp_path: Path,
) -> None:
    script = ROOT / "scripts/install_runner_controller_privileged_gateway.sh"
    install = subprocess.run(
        ["bash", str(script), "--destdir", str(tmp_path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert install.returncode == 0

    install_root = tmp_path / "usr/local/lib/skeleton/runner-controller"
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
request = gateway.build_gateway_request(
    request_id="req-installed-valid",
    idempotency_key="idem-installed-valid",
    expected_main_sha=sha,
    registered_clean_main_sha=sha,
    github_main_sha=sha,
    checkout_path=Path("/home/agent/agent-dev/repos/Skeleton"),
    checkout_head_sha=sha,
    checkout_origin_main_sha=sha,
    issued_at=now,
    expires_at=now + timedelta(seconds=120),
)
calls = []

def runner(request, action):
    calls.append((request["action_id"], action.handler))
    receipt = signer._protected_receipt(
        "DONE",
        "SIGNER_INSTALLATION_VERIFIED",
        expected_main_sha=sha,
        installer_sha256="a" * 64,
        artifacts_ok=True,
    )
    return 0, signer._protected_result("DONE", receipt)

receipt = gateway.execute_gateway_request(request, now=now, runner=runner)
print(json.dumps({{"receipt": receipt, "calls": calls}}, sort_keys=True))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        env={"PATH": os.environ.get("PATH", "")},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["calls"] == [
        [
            gateway.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID,
            "home_edge_esp_lab_stage1_signer_install",
        ]
    ]
    assert payload["receipt"]["status"] == "DONE"
    assert payload["receipt"]["reason"] == "SIGNER_INSTALLATION_VERIFIED"


def test_installed_gateway_import_closure_stays_narrow(tmp_path: Path) -> None:
    script = ROOT / "scripts/install_runner_controller_privileged_gateway.sh"
    install = subprocess.run(
        ["bash", str(script), "--destdir", str(tmp_path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert install.returncode == 0

    install_root = tmp_path / "usr/local/lib/skeleton/runner-controller"
    installed = {
        path.relative_to(install_root).as_posix()
        for path in install_root.rglob("*.py")
    }
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

    assert "core.runner_repository_maintenance_executor" not in imported
    assert not any(name.startswith("core.runner_executor") for name in imported)
    assert not any(name.startswith("core.codex_runtime_recovery") for name in imported)
