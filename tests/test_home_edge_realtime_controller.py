from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.home_edge_exec_mcp_probe import run_probe


ROOT = Path(__file__).resolve().parents[1]


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


def test_installer_runs_protocol_probe_and_live_read_only_probe() -> None:
    installer = (ROOT / "scripts/install_home_edge_realtime_controller.sh").read_text(encoding="utf-8")

    assert "systemctl" not in installer
    assert "home-edge-executor-controller.env" in installer
    assert "IMMUTABLE_BOOTSTRAP=\"/usr/local/lib/skeleton-home-edge-controller/bootstrap/install_home_edge_realtime_controller.sh\"" in installer
    assert "checkout controller installer cannot execute as root" in installer
    assert "500:root:root" in installer
    assert "sha256sum" in installer
    assert "EXPECTED_SOURCE_SHA256" in installer
    assert "reviewed controller source hash mismatch" in installer
    assert "skeleton-home-edge-media-source-snapshot-signer" in installer
    assert "RUNNER_SERVICE_USER=\"agent\"" in installer
    assert "chmod 0750" in installer
    assert "--skip-call" in installer
    assert "skeleton-home-edge-exec-probe" in installer


def test_immutable_bootstrap_model_rejects_checkout_mutation_after_copy() -> None:
    installer = (ROOT / "scripts/install_home_edge_realtime_controller.sh").read_text(encoding="utf-8")

    assert 'rel="${source#"$REPO_ROOT"/}"' in installer
    assert '"core/home_edge/media_source_snapshot.py"' in installer
    assert '"scripts/home_edge_media_source_snapshot_signer.py"' in installer
    assert 'EXPECTED_SOURCE_SHA256[$rel]' in installer
    assert "inert controller file copy hash mismatch" in installer


def test_executor_node_installer_does_not_install_controller_snapshot_signer() -> None:
    installer = (ROOT / "scripts/install_home_edge_executor.sh").read_text(encoding="utf-8")

    assert "media-source-snapshot-signer" not in installer
    assert "home_edge_media_source_snapshot_signer.py" not in installer
