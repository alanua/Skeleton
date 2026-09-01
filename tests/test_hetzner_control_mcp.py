from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from core.hetzner_control_mcp import (
    ACTION_GATE_TOOL,
    RUNNER_PRIVILEGED_TOOL,
    HetznerControlMcpDispatcher,
    handle_jsonrpc_message,
)


ROOT = Path(__file__).resolve().parents[1]
HEAD_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f901234abcd"


class CapturingPrivilegedGateway:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def submit(self, request):
        self.requests.append(request)
        return 0, json.dumps(
            {
                "schema": "skeleton.runner_controller_privileged_receipt.v1",
                "status": "NEEDS_OPERATOR",
                "reason": "SYNTHETIC",
                "action_id": "home_edge_esp_lab_stage1_signer_install",
                "repository": "alanua/Skeleton",
                "target": "runner-controller",
                "request_hash": "hash",
                "private_evidence_exposed": False,
                "stderr_exposed": False,
                "env_exposed": False,
                "private_paths_exposed": False,
                "external_side_effects_executed": False,
                "receipt_hash": "receipt",
            },
            sort_keys=True,
        ).encode()


def dispatcher() -> HetznerControlMcpDispatcher:
    return HetznerControlMcpDispatcher(privileged_gateway=CapturingPrivilegedGateway())


def test_tools_are_minimal_named_gateway_facades_without_exec_arguments() -> None:
    tools = dispatcher().list_tools()
    assert [tool["name"] for tool in tools] == [ACTION_GATE_TOOL, RUNNER_PRIVILEGED_TOOL]

    exposed_properties = {
        property_name
        for tool in tools
        for property_name in tool["inputSchema"].get("properties", {})
    }
    assert "argv" not in exposed_properties
    assert "script" not in exposed_properties
    assert "shell" not in exposed_properties
    assert "secret" not in exposed_properties
    assert "ssh" not in exposed_properties


def test_action_gate_tool_reuses_existing_action_gate_contract() -> None:
    result = dispatcher().call_tool(
        ACTION_GATE_TOOL,
        {
            "action_type": "merge_pull_request",
            "repo": "alanua/Skeleton",
            "pr_number": 3483,
            "expected_head_sha": HEAD_SHA,
            "expected_files": ["core/hetzner_control_mcp.py", "tests/test_hetzner_control_mcp.py"],
            "user_approved": True,
        },
    )

    assert result["schema"] == "skeleton.hetzner_control_mcp.v1"
    assert result["result"]["status"] == "allowed"
    assert result["result"]["reasons"] == []


def test_runner_privileged_tool_delegates_exact_request_to_gateway_transport() -> None:
    gateway = CapturingPrivilegedGateway()
    active = HetznerControlMcpDispatcher(privileged_gateway=gateway)
    request = {"schema": "skeleton.runner_controller_privileged_request.v1", "request_id": "req"}

    result = active.call_tool(RUNNER_PRIVILEGED_TOOL, {"request": request})

    assert gateway.requests == [request]
    assert result["result"]["status"] == "NEEDS_OPERATOR"
    assert result["result"]["receipt"]["reason"] == "SYNTHETIC"


def test_unsupported_tools_fail_closed_before_gateway() -> None:
    gateway = CapturingPrivilegedGateway()
    active = HetznerControlMcpDispatcher(privileged_gateway=gateway)

    result = active.call_tool("shell", {"argv": ["id"]})

    assert result["result"]["status"] == "blocked"
    assert result["result"]["reason"] == "UNSUPPORTED_TOOL"
    assert gateway.requests == []


def test_jsonrpc_boundary_lists_and_calls_tools() -> None:
    active = dispatcher()
    listed = handle_jsonrpc_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, dispatcher=active)
    assert listed is not None
    assert listed["result"]["tools"][0]["name"] == ACTION_GATE_TOOL

    called = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": ACTION_GATE_TOOL,
                "arguments": {
                    "action_type": "merge_pull_request",
                    "repo": "alanua/Skeleton",
                    "pr_number": 3483,
                    "expected_head_sha": "not-a-sha",
                    "expected_files": ["core/hetzner_control_mcp.py"],
                    "user_approved": True,
                },
            },
        },
        dispatcher=active,
    )

    assert called is not None
    payload = json.loads(called["result"]["content"][0]["text"])
    assert payload["result"]["status"] == "blocked"
    assert "expected_head_sha must be a 40-character Git SHA." in payload["result"]["reasons"]


def test_installed_form_launcher_resolves_registered_checkout_outside_repo_cwd(tmp_path: Path) -> None:
    install_root = tmp_path / "installed"
    launcher = install_root / "usr/local/bin/skeleton-control-mcp"
    launcher.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/skeleton_control_mcp.py", launcher)
    launcher.chmod(0o555)

    config = install_root / "usr/local/lib/skeleton/runner-controller/config/checkout.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "schema": "skeleton.runner_controller_checkout_config.v1",
                "repository": "alanua/Skeleton",
                "checkout_path": str(ROOT),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    outside_repo = tmp_path / "outside-repo"
    outside_repo.mkdir()
    messages = "\n".join(
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            "",
        ]
    )

    completed = subprocess.run(
        [sys.executable, str(launcher)],
        input=messages,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=outside_repo,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    assert responses[0]["result"]["serverInfo"]["name"] == "skeleton-control-hetzner"
    tools = responses[1]["result"]["tools"]
    assert [tool["name"] for tool in tools] == [ACTION_GATE_TOOL, RUNNER_PRIVILEGED_TOOL]
    assert len(tools) == 2
