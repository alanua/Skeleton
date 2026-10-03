from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from core.awareness_context import AWARENESS_CONTEXT_SCHEMA, PRIVACY_BOUNDARY
from core.hetzner_control_mcp import (
    ACTION_GATE_TOOL,
    AWARENESS_CONTEXT_TOOL,
    RUNNER_PRIVILEGED_TOOL,
    AwarenessContextProviderError,
    HetznerControlMcpDispatcher,
    PrivateHandoffAwarenessContextProvider,
    handle_jsonrpc_message,
)
from core.memory_bootstrap import (
    MEMORY_BOOTSTRAP_RESPONSE_SCHEMA,
    PRIVATE_CONTEXT_ENV,
    PRIVATE_CONTEXT_MARKER,
)
from core.private_memory_history import content_hash


ROOT = Path(__file__).resolve().parents[1]
HEAD_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f901234abcd"


class CapturingAwarenessProvider:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def read(self, request):
        self.requests.append(request)
        return {
            "packet": {
                "schema": AWARENESS_CONTEXT_SCHEMA,
                "project_id": request.project_id,
                "task_route": request.task_route,
                "private_value": "synthetic-private",
            },
            "receipt": {
                "schema": "skeleton.awareness_context.public_receipt.v1",
                "public_safe": True,
            },
        }


class FailingAwarenessProvider:
    def read(self, request):
        raise AwarenessContextProviderError("AWARENESS_PRIVATE_HANDOFF_UNAVAILABLE")


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
    return HetznerControlMcpDispatcher(
        privileged_gateway=CapturingPrivilegedGateway(),
        awareness_provider=CapturingAwarenessProvider(),
    )


def test_tools_are_minimal_named_gateway_facades_without_exec_arguments() -> None:
    tools = dispatcher().list_tools()
    assert [tool["name"] for tool in tools] == [
        ACTION_GATE_TOOL,
        RUNNER_PRIVILEGED_TOOL,
        AWARENESS_CONTEXT_TOOL,
    ]

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


def test_awareness_tool_delegates_to_read_only_provider_and_returns_private_packet() -> None:
    provider = CapturingAwarenessProvider()
    active = HetznerControlMcpDispatcher(
        privileged_gateway=CapturingPrivilegedGateway(),
        awareness_provider=provider,
    )

    result = active.call_tool(
        AWARENESS_CONTEXT_TOOL,
        {
            "project_id": "skeleton",
            "task_route": "runner",
            "task_body": "Continue the current Skeleton task.",
            "requested_capabilities": ["memory"],
        },
    )

    assert result["result"]["status"] == "DONE"
    assert result["result"]["awareness"]["private_value"] == "synthetic-private"
    assert result["result"]["receipt"]["public_safe"] is True
    assert len(provider.requests) == 1
    assert provider.requests[0].project_id == "skeleton"
    assert provider.requests[0].requested_capabilities == ("memory",)


def test_awareness_provider_failure_is_public_safe() -> None:
    active = HetznerControlMcpDispatcher(
        privileged_gateway=CapturingPrivilegedGateway(),
        awareness_provider=FailingAwarenessProvider(),
    )

    result = active.call_tool(
        AWARENESS_CONTEXT_TOOL,
        {
            "project_id": "skeleton",
            "task_route": "runner",
            "task_body": "Continue.",
        },
    )

    assert result["result"] == {
        "status": "blocked",
        "reason": "AWARENESS_PRIVATE_HANDOFF_UNAVAILABLE",
    }


def test_private_handoff_awareness_provider_reuses_existing_boot_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    packet = {
        "schema": AWARENESS_CONTEXT_SCHEMA,
        "project_id": "skeleton",
        "task_route": "runner",
        "privacy_boundary": PRIVACY_BOUNDARY,
        "derivation_mode": "ephemeral_read_only_derived_packet",
        "checked_at": "2026-10-03T10:00:00Z",
        "sections": {
            "capability_truth": {
                "records": [{"capability_id": "memory"}],
                "section_hash": "b" * 64,
            }
        },
        "labels": {"freshness": "FRESH"},
        "runtime_mutation_performed": False,
        "canonical_store_mutation_performed": False,
    }
    packet["awareness_hash"] = content_hash(packet)
    handoff = tmp_path / "context.json"
    handoff.write_text(
        json.dumps(
            {
                "schema": MEMORY_BOOTSTRAP_RESPONSE_SCHEMA,
                "marker": PRIVATE_CONTEXT_MARKER,
                "awareness": packet,
            }
        ),
        encoding="utf-8",
    )
    handoff.chmod(0o600)
    monkeypatch.setenv(PRIVATE_CONTEXT_ENV, str(handoff))

    provider = PrivateHandoffAwarenessContextProvider()
    request = type(
        "Request",
        (),
        {
            "project_id": "skeleton",
            "task_route": "runner",
            "task_body": "Continue.",
            "requested_capabilities": ("memory",),
        },
    )()
    result = provider.read(request)

    assert result["packet"]["awareness_hash"] == packet["awareness_hash"]
    assert result["receipt"]["public_safe"] is True
    assert result["receipt"]["private_payloads_included"] is False
    assert "private_value" not in json.dumps(result["receipt"])


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
    assert len(tools) == 3
