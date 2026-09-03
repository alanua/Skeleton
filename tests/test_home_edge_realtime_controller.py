from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.home_edge_exec_mcp_probe import run_probe


ROOT = Path(__file__).resolve().parents[1]
STALE_REPO_ROOT = "/home/agent/agent-dev/repos/Skeleton"


def test_mcp_probe_initializes_lists_and_calls_tool(tmp_path: Path) -> None:
    fake_server = tmp_path / "fake_mcp.py"
    fake_server.write_text(
        """
import json
import sys

for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "notifications/initialized":
        continue
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": [{"name": "home_edge_exec", "inputSchema": {"type": "object"}}]}
    elif method == "tools/call":
        receipt = {"status": "ok", "receipt_hash": "public-hash"}
        result = {"content": [{"type": "text", "text": json.dumps(receipt)}], "isError": False}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -1, "message": "unsupported"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "result": result}), flush=True)
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = run_probe([sys.executable, str(fake_server)], timeout_seconds=3)

    assert result.initialized is True
    assert result.tool_listed is True
    assert result.call_status == "ok"
    assert result.receipt_hash == "public-hash"
    assert result.latency_ms is not None


def test_registration_template_uses_installed_stdio_launcher() -> None:
    config = json.loads((ROOT / "config/mcp/skeleton-home-edge-exec.json").read_text(encoding="utf-8"))

    server = config["mcpServers"]["home-edge-exec"]
    assert server["command"] == "/usr/local/bin/skeleton-home-edge-exec-mcp"
    assert server["args"] == []
    assert set(server) == {"command", "args"}


def test_launcher_reuses_existing_private_runtime_files() -> None:
    launcher = (ROOT / "scripts/home_edge_exec_mcp_launcher.sh").read_text(encoding="utf-8")

    assert "/etc/skeleton/home-edge-01.env" in launcher
    assert "/etc/skeleton/home-edge-executor-controller.env" in launcher
    assert "home_edge_exec_mcp.py" in launcher
    assert "github" not in launcher.lower()


def test_launcher_has_no_stale_controller_repo_root_default() -> None:
    launcher = (ROOT / "scripts/home_edge_exec_mcp_launcher.sh").read_text(encoding="utf-8")

    assert STALE_REPO_ROOT not in launcher
    assert "SKELETON_HOME_EDGE_REPO_ROOT" in launcher
    assert "BASH_SOURCE" in launcher


def test_installer_embeds_selected_repo_root_in_installed_launcher() -> None:
    installer = (ROOT / "scripts/install_home_edge_realtime_controller.sh").read_text(encoding="utf-8")

    assert STALE_REPO_ROOT not in installer
    assert "installed-launcher" in installer
    assert "export SKELETON_HOME_EDGE_REPO_ROOT=%q" in installer
    assert "home_edge_exec_mcp_launcher.sh" in installer


def test_installer_runs_protocol_probe_and_live_read_only_probe() -> None:
    installer = (ROOT / "scripts/install_home_edge_realtime_controller.sh").read_text(encoding="utf-8")

    assert "systemctl" not in installer
    assert "home-edge-executor-controller.env" in installer
    assert "--skip-call" in installer
    assert "skeleton-home-edge-exec-probe" in installer
