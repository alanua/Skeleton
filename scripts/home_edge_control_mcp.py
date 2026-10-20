#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.home_edge.executor import HomeEdgeExecRequest, sign_request
from core.home_edge.executor_gateway import EXEC_HMAC_SECRET_ENV, execute_home_edge_request

MEDIA_CONTROL_TOOL = "home_media_control"
MEDIA_STATUS_TOOL = "home_media_status"
MODE_KEYS = {
    "android_tv": "1",
    "chrome": "2",
    "kiosk": "3",
    "vlc": "4",
    "games": "5",
    "off": "0",
}
ALLOWED_CONTROL_KEYS = {"mode", "volume_percent", "idempotency_key"}


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        response = handle_message(json.loads(line))
        if response is not None:
            print(json.dumps(response, sort_keys=True, separators=(",", ":")), flush=True)
    return 0


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    msg_id = message.get("id")
    try:
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "skeleton-home-media-control", "version": "0.1.0"},
                    "instructions": (
                        "Use home_media_status for read-only inspection. Use home_media_control only when "
                        "the user explicitly asks to change the media mode or volume. Modes are android_tv, "
                        "chrome, kiosk, vlc, games, and off. Do not infer arbitrary shell commands."
                    ),
                },
            }
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": [_status_tool_description(), _control_tool_description()]},
            }
        if method == "tools/call":
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            name = params.get("name")
            args = params.get("arguments")
            if not isinstance(args, dict):
                raise ValueError("tool arguments must be an object")
            if name == MEDIA_STATUS_TOOL:
                if args:
                    raise ValueError("home_media_status does not accept arguments")
                payload = _execute_media(status_only=True, mode=None, volume_percent=None, idempotency_key=None)
            elif name == MEDIA_CONTROL_TOOL:
                payload = _handle_control(args)
            else:
                raise ValueError("unknown tool")
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(payload, sort_keys=True)}],
                    "structuredContent": payload,
                    "isError": payload.get("status") != "ok",
                },
            }
        raise ValueError(f"unsupported method: {method}")
    except Exception as exc:  # noqa: BLE001 - MCP errors must remain bounded.
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32000, "message": f"{type(exc).__name__}: {exc}"},
        }


def _handle_control(args: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(args) - ALLOWED_CONTROL_KEYS)
    if unknown:
        raise ValueError(f"unsupported control fields: {', '.join(unknown)}")
    mode = args.get("mode")
    volume = args.get("volume_percent")
    idempotency_key = args.get("idempotency_key")
    if mode is None and volume is None:
        raise ValueError("mode or volume_percent is required")
    if mode is not None and mode not in MODE_KEYS:
        raise ValueError("unsupported media mode")
    if volume is not None and (not isinstance(volume, int) or isinstance(volume, bool) or not 0 <= volume <= 100):
        raise ValueError("volume_percent must be an integer from 0 to 100")
    if idempotency_key is not None and (not isinstance(idempotency_key, str) or not idempotency_key.strip()):
        raise ValueError("idempotency_key must be a non-empty string")
    return _execute_media(
        status_only=False,
        mode=mode,
        volume_percent=volume,
        idempotency_key=idempotency_key,
    )


def _execute_media(
    *,
    status_only: bool,
    mode: str | None,
    volume_percent: int | None,
    idempotency_key: str | None,
) -> dict[str, Any]:
    request_id = f"home-media-{uuid4()}"
    lane = "read_only" if status_only else "routine_mutation"
    request_data: dict[str, Any] = {
        "request_id": request_id,
        "node_id": "home-edge-01",
        "execution_lane": lane,
        "run_as": "desktop-user",
        "mode": "script",
        "script_interpreter": "python3",
        "script": _remote_media_script(mode=mode, volume_percent=volume_percent),
        "timeout_seconds": 20,
        "timestamp": datetime.now(UTC).isoformat(),
        "nonce": f"home-media-{uuid4()}",
        "public": False,
    }
    if not status_only:
        request_data["operator_approval_ref"] = f"chatgpt-home-media:{request_id}"
        request_data["idempotency_key"] = idempotency_key or request_id

    secret = os.environ.get(EXEC_HMAC_SECRET_ENV, "").strip()
    if not secret:
        raise ValueError("private MCP signing secret is not configured")
    request = HomeEdgeExecRequest.from_mapping(request_data)
    outbound = request.to_mapping(include_signature=False)
    outbound["signature"] = sign_request(request, secret)
    receipt = execute_home_edge_request(outbound).to_mapping()
    if receipt.get("status") != "ok" or receipt.get("exit_code") != 0:
        return {
            "schema": "skeleton.home_media.result.v1",
            "status": "blocked",
            "action": "status" if status_only else "control",
            "receipt_hash": receipt.get("receipt_hash"),
            "duration_seconds": receipt.get("duration_seconds"),
        }
    stdout = receipt.get("stdout", "")
    try:
        result = json.loads(stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise ValueError("Home Edge media readback was invalid") from exc
    if not isinstance(result, dict):
        raise ValueError("Home Edge media readback must be an object")
    return {
        "schema": "skeleton.home_media.result.v1",
        "status": "ok",
        "action": "status" if status_only else "control",
        **result,
        "receipt_hash": receipt.get("receipt_hash"),
        "duration_seconds": receipt.get("duration_seconds"),
    }


def _remote_media_script(*, mode: str | None, volume_percent: int | None) -> str:
    return f'''
import ast
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

REQUESTED_MODE = {json.dumps(mode)}
REQUESTED_VOLUME = {json.dumps(volume_percent)}
MODE_KEYS = {json.dumps(MODE_KEYS, sort_keys=True)}
STATE_FILE = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "skeleton-home-media-state.json"


def run(argv, *, timeout=5, check=False):
    completed = subprocess.run(
        argv,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if check and completed.returncode != 0:
        raise SystemExit("bounded command failed")
    return completed


def gsettings_get(schema, key):
    completed = run(["/usr/bin/gsettings", "get", schema, key], check=True)
    return completed.stdout.strip()


def normalize_binding(value):
    return re.sub(r"\\s+", "", value).lower()


def shortcut_inventory():
    raw = gsettings_get("org.gnome.settings-daemon.plugins.media-keys", "custom-keybindings")
    try:
        paths = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        raise SystemExit("custom shortcut inventory is invalid")
    if not isinstance(paths, list):
        raise SystemExit("custom shortcut inventory is not a list")
    inventory = {{}}
    for path in paths:
        if not isinstance(path, str):
            continue
        schema = f"org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:{{path}}"
        binding = gsettings_get(schema, "binding").strip("'")
        command = gsettings_get(schema, "command").strip("'")
        inventory[normalize_binding(binding)] = command
    return inventory


def resolve_shortcut(mode):
    key = MODE_KEYS[mode]
    wanted = {{
        f"<super><alt>{{key}}",
        f"<alt><super>{{key}}",
    }}
    inventory = shortcut_inventory()
    for binding in wanted:
        command = inventory.get(normalize_binding(binding))
        if command:
            return binding, command
    raise SystemExit("requested media shortcut is not configured")


def set_volume(percent):
    value = f"{{percent / 100:.2f}}"
    run(["/usr/bin/wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", value], check=True)


def get_volume():
    completed = run(["/usr/bin/wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], check=True)
    match = re.search(r"Volume:\\s*([0-9.]+)", completed.stdout)
    if match is None:
        raise SystemExit("volume readback unavailable")
    value = float(match.group(1))
    return round(value * 100), "[MUTED]" in completed.stdout


def process_hint():
    completed = run(["/usr/bin/ps", "-u", str(os.getuid()), "-o", "comm=,args="], timeout=5)
    text = completed.stdout.lower()
    game_markers = (
        "retroarch",
        "emulationstation",
        "es-de",
        "pegasus-fe",
        "pegasus-frontend",
        "lutris",
        "steam",
        "heroic",
        "attract",
        "retrofe",
        "gamehub",
        "scummvm",
    )
    if any(marker in text for marker in game_markers):
        return "games"
    if "waydroid show-full-ui" in text:
        return "android_tv"
    if re.search(r"(^|\\n)\\s*vlc\\s", text):
        return "vlc"
    chrome_lines = [line for line in text.splitlines() if "chrome" in line or "chromium" in line]
    if any("--kiosk" in line for line in chrome_lines):
        return "kiosk"
    if chrome_lines:
        return "chrome"
    return "off"


def read_state():
    if not STATE_FILE.exists():
        return None
    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("mode") if isinstance(payload, dict) else None
    return value if value in MODE_KEYS else None


def write_state(mode, binding, command):
    payload = {{
        "mode": mode,
        "binding": binding,
        "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
        "updated_at": time.time(),
    }}
    STATE_FILE.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\\n", encoding="utf-8")
    STATE_FILE.chmod(0o600)


resolved_binding = None
command_hash = None
if REQUESTED_MODE is not None:
    resolved_binding, command = resolve_shortcut(REQUESTED_MODE)
    command_hash = hashlib.sha256(command.encode()).hexdigest()
    completed = run(["/bin/bash", "-lc", command], timeout=12)
    if completed.returncode != 0:
        raise SystemExit("media shortcut command failed")
    write_state(REQUESTED_MODE, resolved_binding, command)
    time.sleep(0.8)

if REQUESTED_VOLUME is not None:
    set_volume(REQUESTED_VOLUME)
    time.sleep(0.2)

volume_percent, muted = get_volume()
selected_mode = read_state()
active_hint = process_hint()
print(json.dumps({{
    "requested_mode": REQUESTED_MODE,
    "selected_mode": selected_mode,
    "active_mode_hint": active_hint,
    "resolved_shortcut": resolved_binding,
    "shortcut_command_hash": command_hash,
    "volume_percent": volume_percent,
    "muted": muted,
    "available_modes": sorted(MODE_KEYS),
}}, sort_keys=True, separators=(",", ":")))
'''.strip()


def _status_tool_description() -> dict[str, Any]:
    return {
        "name": MEDIA_STATUS_TOOL,
        "description": (
            "Read the current Home Edge media mode hint, selected mode marker, default audio volume, "
            "mute state, and configured media modes. Use this before or after a control action when "
            "the user asks what is active. This tool does not change anything."
        ),
        "annotations": {
            "title": "Read home media status",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    }


def _control_tool_description() -> dict[str, Any]:
    return {
        "name": MEDIA_CONTROL_TOOL,
        "description": (
            "Change the Home Edge TV/media mode and/or default audio volume. Use only when the user "
            "explicitly requests a change. Modes: android_tv (Super+Alt+1), chrome (Super+Alt+2), "
            "kiosk (Super+Alt+3), vlc (Super+Alt+4), games (Super+Alt+5), off (Super+Alt+0). At least "
            "one of mode or volume_percent is required. This tool cannot run arbitrary commands."
        ),
        "annotations": {
            "title": "Control home media",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": list(MODE_KEYS)},
                "volume_percent": {"type": "integer", "minimum": 0, "maximum": 100},
                "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 200},
            },
            "anyOf": [{"required": ["mode"]}, {"required": ["volume_percent"]}],
            "additionalProperties": False,
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
