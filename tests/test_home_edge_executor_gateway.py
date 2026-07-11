from __future__ import annotations

import json
import os
import pwd
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.home_edge.executor import HomeEdgeExecRequest, HomeEdgeExecReceipt, PUBLIC_ERROR_MESSAGE, receipt_from_mapping, sign_request
from core.home_edge.executor_gateway import LocalExecTransport, OpenSSHExecTransport, execute_home_edge_request
from core.home_edge.profile import load_home_edge_profile
from scripts import home_edge_exec
from scripts.home_edge_exec_mcp import TOOL_NAME, handle_message

SECRET = "gateway-secret"


@pytest.fixture(autouse=True)
def node_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SKELETON_HOME_EDGE_EXEC_HMAC_SECRET", SECRET)
    monkeypatch.setenv("SKELETON_HOME_EDGE_EXEC_IDEMPOTENCY_CACHE", str(tmp_path / "state.json"))
    monkeypatch.setenv("SKELETON_HOME_EDGE_DESKTOP_USER", pwd.getpwuid(os.geteuid()).pw_name)


def signed(data: dict[str, object]) -> dict[str, object]:
    data = {
        "timestamp": datetime.now(UTC).isoformat(),
        "nonce": str(data.get("request_id", "nonce")),
        "run_as": "desktop-user",
        **data,
    }
    data["signature"] = sign_request(HomeEdgeExecRequest.from_mapping(data), SECRET)
    return data


def test_gateway_uses_injected_transport_without_github_or_runner_polling() -> None:
    receipt = execute_home_edge_request(
        signed({
            "request_id": "gateway-1",
            "node_id": "home-edge-01",
            "execution_lane": "read_only",
            "argv": [sys.executable, "-c", "print('transport')"],
            "timeout_seconds": 5,
        }),
        transport=LocalExecTransport(),
    )

    assert receipt.status == "ok"
    assert receipt.stdout.strip() == "transport"


def test_public_transport_failure_returns_only_generic_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE", "/private/id_ed25519")
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE", "/private/known_hosts")

    class Completed:
        returncode = 255
        stdout = ""
        stderr = "ssh: connect to user@100.64.0.10 failed; /private/key SECRET_NAME=abc command uname"

    monkeypatch.setattr("core.home_edge.executor_gateway.subprocess.run", lambda *_args, **_kwargs: Completed())
    request = signed({
        "request_id": "public-transport",
        "node_id": "home-edge-01",
        "execution_lane": "read_only",
        "argv": ["uname", "-a"],
        "timeout_seconds": 5,
        "public": True,
    })

    with pytest.raises(Exception) as exc_info:
        execute_home_edge_request(request, profile=load_home_edge_profile())

    message = str(exc_info.value)
    assert message == PUBLIC_ERROR_MESSAGE
    assert "100.64.0.10" not in message
    assert "/private" not in message
    assert "SECRET_NAME" not in message
    assert "uname" not in message


def test_private_transport_failure_retains_bounded_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE", "/private/id_ed25519")
    monkeypatch.setenv("SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE", "/private/known_hosts")

    class Completed:
        returncode = 255
        stdout = ""
        stderr = "ssh private diagnostic stderr"

    monkeypatch.setattr("core.home_edge.executor_gateway.subprocess.run", lambda *_args, **_kwargs: Completed())
    request = signed({
        "request_id": "private-transport",
        "node_id": "home-edge-01",
        "execution_lane": "read_only",
        "argv": ["uname", "-a"],
        "timeout_seconds": 5,
    })

    with pytest.raises(Exception) as exc_info:
        OpenSSHExecTransport(load_home_edge_profile()).execute(request, timeout_seconds=10)

    assert "remote home_edge_exec failed: ssh private diagnostic stderr" in str(exc_info.value)


def test_public_receipt_stays_public_after_deserialization_and_mcp_serialization(monkeypatch: pytest.MonkeyPatch) -> None:
    private_receipt = HomeEdgeExecReceipt(
        status="failed",
        request_id="public-roundtrip",
        node_id="home-edge-01",
        execution_lane="read_only",
        exit_code=1,
        stdout="private stdout /private/path",
        stderr="private stderr SECRET_NAME=abc",
        started_at=datetime.now(UTC).isoformat(),
        finished_at=datetime.now(UTC).isoformat(),
        duration_seconds=0.1,
        idempotency="executed",
        receipt_hash="hash",
        error=None,
        argv=("secret-command",),
        public=True,
    )
    controller_receipt = receipt_from_mapping(private_receipt.to_mapping())
    assert controller_receipt.public is True
    assert "stdout" not in controller_receipt.to_mapping()

    monkeypatch.setattr("scripts.home_edge_exec_mcp.execute_home_edge_request", lambda _args: controller_receipt)
    response = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": TOOL_NAME,
                "arguments": {
                    "node_id": "home-edge-01",
                    "execution_lane": "read_only",
                    "argv": ["secret-command"],
                    "timeout_seconds": 5,
                    "public": True,
                },
            },
        }
    )

    text = response["result"]["content"][0]["text"]
    assert '"public": true' in text
    assert "stdout" not in text
    assert "stderr" not in text
    assert "secret-command" not in text
    assert "/private" not in text
    assert "SECRET_NAME" not in text


def test_mcp_server_exposes_one_standards_tool(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_execute(args):
        captured.update(args)

        class Receipt:
            def to_mapping(self):
                return {"status": "ok", "stdout": "done"}

        return Receipt()

    monkeypatch.setattr("scripts.home_edge_exec_mcp.execute_home_edge_request", fake_execute)
    listed = handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    called = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": TOOL_NAME,
                "arguments": {
                    "node_id": "home-edge-01",
                    "execution_lane": "read_only",
                    "argv": ["true"],
                    "timeout_seconds": 5,
                },
            },
        }
    )

    assert listed["result"]["tools"][0]["name"] == "home_edge_exec"
    assert captured["argv"] == ["true"]
    assert called["result"]["isError"] is False


def test_mcp_public_error_is_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_execute(_args):
        raise RuntimeError("raw ssh stderr /private/path SECRET_NAME=abc command uname")

    monkeypatch.setattr("scripts.home_edge_exec_mcp.execute_home_edge_request", fake_execute)
    response = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": TOOL_NAME,
                "arguments": {
                    "node_id": "home-edge-01",
                    "execution_lane": "read_only",
                    "argv": ["uname"],
                    "timeout_seconds": 5,
                    "public": True,
                },
            },
        }
    )

    assert response["error"]["message"] == PUBLIC_ERROR_MESSAGE


def test_controller_cli_public_error_is_generic(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fake_execute(_args):
        raise RuntimeError("raw ssh stderr /private/path SECRET_NAME=abc command uname")

    monkeypatch.setattr(home_edge_exec, "execute_home_edge_request", fake_execute)
    code = home_edge_exec.main(
        [
            "--public",
            "--request-id",
            "cli-public",
            "--node-id",
            "home-edge-01",
            "--lane",
            "read_only",
            "--timeout-seconds",
            "5",
            "--",
            "uname",
            "-a",
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert json.loads(captured.err)["error"] == PUBLIC_ERROR_MESSAGE
    assert "/private" not in captured.err
    assert "SECRET_NAME" not in captured.err
    assert "uname" not in captured.err


def test_systemd_unit_starts_real_executor_server() -> None:
    text = Path("scripts/skeleton-home-edge-executor.service").read_text(encoding="utf-8")

    assert "home_edge_exec --server" in text
    assert "ExecStart=" not in text
    assert "Restart=" not in text


def test_installer_secret_modes_idempotency_wrapper_and_no_service_enable(tmp_path: Path) -> None:
    install_root = tmp_path / "root"
    desktop = pwd.getpwuid(os.geteuid()).pw_name
    installer = Path("scripts/install_home_edge_executor.sh")

    first = subprocess.run(
        [str(installer), "--root", str(install_root), "--desktop-user", desktop, "--replace-secret-stdin"],
        input="test-hmac-value",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    assert "test-hmac-value" not in first.stdout
    assert "test-hmac-value" not in first.stderr

    env_file = install_root / "etc/skeleton/home_edge_executor.env"
    state_dir = install_root / "var/lib/skeleton/home_edge_exec"
    wrapper = install_root / "usr/local/bin/home_edge_exec"
    assert oct(stat.S_IMODE(env_file.stat().st_mode)) == "0o600"
    assert oct(stat.S_IMODE(state_dir.stat().st_mode)) == "0o700"
    assert "test-hmac-value" in env_file.read_text(encoding="utf-8")
    assert wrapper.exists()

    second = subprocess.run(
        [str(installer), "--root", str(install_root), "--desktop-user", desktop],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert second.returncode == 0, second.stderr
    assert "test-hmac-value" in env_file.read_text(encoding="utf-8")

    wrapper_text = wrapper.read_text(encoding="utf-8")
    assert "home_edge_exec.py\" --server" in wrapper_text
    assert "systemctl enable" not in installer.read_text(encoding="utf-8")
    assert "Restart=" not in installer.read_text(encoding="utf-8")

    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "nonce": "wrapper-1",
        "run_as": "desktop-user",
        "request_id": "wrapper-1",
        "node_id": "home-edge-01",
        "execution_lane": "read_only",
        "argv": [sys.executable, "-c", "print('wrapper-ok')"],
        "timeout_seconds": 5,
    }
    payload["signature"] = sign_request(HomeEdgeExecRequest.from_mapping(payload), "test-hmac-value")
    ran = subprocess.run(
        [str(wrapper), "--server"],
        input=json.dumps(payload),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert ran.returncode == 0, ran.stderr
    receipt = json.loads(ran.stdout)
    assert receipt["status"] == "ok"
    assert receipt["stdout"].strip() == "wrapper-ok"
