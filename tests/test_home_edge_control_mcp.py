from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from core.home_edge.executor import HomeEdgeExecRequest, sign_request
from scripts import home_edge_control_mcp
from scripts.home_edge_control_mcp import (
    MEDIA_CONTROL_TOOL,
    MEDIA_STATUS_TOOL,
    MODE_KEYS,
    handle_message,
)
from scripts.home_edge_control_mcp_probe import run_probe

ROOT = Path(__file__).resolve().parents[1]
SECRET = "media-control-test-secret"


def _call(name: str, arguments: dict) -> dict:
    response = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    assert response is not None
    return response


def test_tools_list_exposes_only_bounded_media_tools() -> None:
    response = handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert response is not None
    tools = response["result"]["tools"]
    assert {tool["name"] for tool in tools} == {MEDIA_STATUS_TOOL, MEDIA_CONTROL_TOOL}
    encoded = json.dumps(tools, sort_keys=True)
    for forbidden in ("argv", "script_interpreter", "environment", "cwd", "signature", "secret"):
        assert forbidden not in encoded


def test_exact_media_mode_mapping() -> None:
    assert MODE_KEYS == {
        "chrome": "1",
        "android_tv": "2",
        "vlc": "3",
        "kiosk": "4",
        "off": "0",
    }


def test_control_rejects_arbitrary_execution_fields() -> None:
    response = _call(MEDIA_CONTROL_TOOL, {"mode": "chrome", "argv": ["uname", "-a"]})
    assert response["error"]["code"] == -32000
    assert "unsupported control fields" in response["error"]["message"]


@pytest.mark.parametrize("value", [-1, 101, 50.5, True, "80"])
def test_control_rejects_invalid_volume(value: object) -> None:
    response = _call(MEDIA_CONTROL_TOOL, {"volume_percent": value})
    assert response["error"]["code"] == -32000
    assert "volume_percent" in response["error"]["message"]


def test_control_builds_signed_routine_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class Receipt:
        def to_mapping(self) -> dict[str, object]:
            return {
                "status": "ok",
                "exit_code": 0,
                "stdout": json.dumps(
                    {
                        "requested_mode": "android_tv",
                        "selected_mode": "android_tv",
                        "active_mode_hint": "android_tv",
                        "resolved_shortcut": "<super><alt>2",
                        "shortcut_command_hash": "hash",
                        "volume_percent": 80,
                        "muted": False,
                        "available_modes": sorted(MODE_KEYS),
                    }
                ),
                "receipt_hash": "receipt",
                "duration_seconds": 0.8,
            }

    def fake_execute(request: dict[str, object]) -> Receipt:
        captured.update(request)
        return Receipt()

    monkeypatch.setenv("SKELETON_HOME_EDGE_EXEC_HMAC_SECRET", SECRET)
    monkeypatch.setattr(home_edge_control_mcp, "execute_home_edge_request", fake_execute)

    response = _call(
        MEDIA_CONTROL_TOOL,
        {"mode": "android_tv", "volume_percent": 80, "idempotency_key": "media-android-80"},
    )

    assert response["result"]["isError"] is False
    payload = response["result"]["structuredContent"]
    assert payload["selected_mode"] == "android_tv"
    assert payload["volume_percent"] == 80
    assert captured["execution_lane"] == "routine_mutation"
    assert captured["run_as"] == "desktop-user"
    assert captured["mode"] == "script"
    assert captured["script_interpreter"] == "python3"
    assert captured["idempotency_key"] == "media-android-80"
    assert str(captured["operator_approval_ref"]).startswith("chatgpt-home-media:")
    assert "gsettings" in str(captured["script"])
    assert "wpctl" in str(captured["script"])
    assert captured["signature"] == sign_request(HomeEdgeExecRequest.from_mapping(captured), SECRET)


def test_status_builds_read_only_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class Receipt:
        def to_mapping(self) -> dict[str, object]:
            return {
                "status": "ok",
                "exit_code": 0,
                "stdout": json.dumps(
                    {
                        "requested_mode": None,
                        "selected_mode": "chrome",
                        "active_mode_hint": "chrome",
                        "resolved_shortcut": None,
                        "shortcut_command_hash": None,
                        "volume_percent": 100,
                        "muted": False,
                        "available_modes": sorted(MODE_KEYS),
                    }
                ),
                "receipt_hash": "status-receipt",
                "duration_seconds": 0.4,
            }

    def fake_execute(request: dict[str, object]) -> Receipt:
        captured.update(request)
        return Receipt()

    monkeypatch.setenv("SKELETON_HOME_EDGE_EXEC_HMAC_SECRET", SECRET)
    monkeypatch.setattr(home_edge_control_mcp, "execute_home_edge_request", fake_execute)

    response = _call(MEDIA_STATUS_TOOL, {})

    assert response["result"]["structuredContent"]["status"] == "ok"
    assert captured["execution_lane"] == "read_only"
    assert "operator_approval_ref" not in captured
    assert "idempotency_key" not in captured


def test_probe_initializes_lists_and_calls_status(tmp_path: Path) -> None:
    fake_server = tmp_path / "fake_media_mcp.py"
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
        result = {"tools": [
            {"name": "home_media_control", "inputSchema": {"type": "object"}},
            {"name": "home_media_status", "inputSchema": {"type": "object"}},
        ]}
    elif method == "tools/call":
        result = {
            "content": [{"type": "text", "text": "{}"}],
            "structuredContent": {"status": "ok", "receipt_hash": "public-hash"},
            "isError": False,
        }
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "result": result}), flush=True)
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = run_probe([sys.executable, str(fake_server)], timeout_seconds=3)

    assert result.initialized is True
    assert set(result.tools) == {MEDIA_STATUS_TOOL, MEDIA_CONTROL_TOOL}
    assert result.status_call == "ok"
    assert result.receipt_hash == "public-hash"
    assert result.latency_ms is not None


def test_registration_uses_only_bounded_launcher() -> None:
    config = json.loads((ROOT / "config/mcp/skeleton-home-media-control.json").read_text(encoding="utf-8"))
    server = config["mcpServers"]["home-media-control"]
    assert server == {"command": "/usr/local/bin/skeleton-home-media-control-mcp", "args": []}
