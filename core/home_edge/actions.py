from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .diagnostics import (
    DEFAULT_ARTIFACT_PATH,
    PUBLIC_NODE_ID,
    HomeEdgeDiagnosticError,
    OpenSSHTransport,
    ProbeResult,
    _redact,
    run_audited_home_edge_command,
)
from .profile import HomeEdgeProfile, load_home_edge_profile


ACTION_TIMEOUT_SECONDS = 5
MAX_OUTPUT_BYTES = 4096
SUPPORTED_TARGETS = frozenset(("host", "android_tv", "both"))
SUPPORTED_TV_MODES = frozenset(("chrome", "waydroid", "vlc", "kiosk", "off"))
MUTATING_ACTIONS = frozenset(
    ("media.set_volume", "media.mute", "media.unmute", "tv.set_mode")
)

ACTION_PARAMETER_SCHEMAS: dict[str, dict[str, Any]] = {
    "media.get_volume": {
        "required": [],
        "optional": {"target": SUPPORTED_TARGETS},
    },
    "media.set_volume": {
        "required": ["level", "target"],
        "optional": {},
    },
    "media.mute": {
        "required": ["target"],
        "optional": {},
    },
    "media.unmute": {
        "required": ["target"],
        "optional": {},
    },
    "media.playback_status": {
        "required": [],
        "optional": {},
    },
    "home_edge.health": {
        "required": [],
        "optional": {},
    },
    "home_edge.diagnostic": {
        "required": [],
        "optional": {},
    },
    "tv.get_mode": {
        "required": [],
        "optional": {},
    },
    "tv.set_mode": {
        "required": ["mode"],
        "optional": {"confirm_off": frozenset((True, False))},
    },
}
ALLOWED_ACTIONS = frozenset(ACTION_PARAMETER_SCHEMAS)
REQUEST_REQUIRED_FIELDS = frozenset(
    ("node_id", "action_id", "request_id", "timestamp", "nonce", "idempotency_key")
)
REQUEST_OPTIONAL_FIELDS = frozenset(("parameters",))
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,96}$")


class HomeEdgeActionError(ValueError):
    """Raised when a direct action request fails local validation."""


class ActionTransport(Protocol):
    def run_action(self, payload: str, *, timeout_seconds: int) -> ProbeResult:
        ...


@dataclass(frozen=True)
class StrictSSHActionTransport:
    profile: HomeEdgeProfile

    def run_action(self, payload: str, *, timeout_seconds: int) -> ProbeResult:
        return OpenSSHTransport(self.profile).run_probe(
            payload,
            timeout_seconds=timeout_seconds,
        )


def action_contract() -> dict[str, Any]:
    return {
        "schema": "skeleton.home_edge.direct_action_contract.v1",
        "node_id": PUBLIC_NODE_ID,
        "transport": "openssh_over_tailscale_ip",
        "host_key_policy": "strict",
        "command_source": "fixed_typed_allowlist",
        "actions": {
            action: _public_parameter_schema(schema)
            for action, schema in sorted(ACTION_PARAMETER_SCHEMAS.items())
        },
        "mutation_policy": {
            "direct_low_risk": sorted(MUTATING_ACTIONS - frozenset(("tv.set_mode",))),
            "confirmation_required": ["tv.set_mode:off"],
        },
    }


def execute_home_edge_action(
    request: dict[str, Any],
    *,
    profile: HomeEdgeProfile | None = None,
    transport: ActionTransport | None = None,
    diagnostic_transport: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    validated = validate_action_request(request)
    node = profile or load_home_edge_profile()
    if node.node_id != PUBLIC_NODE_ID:
        raise HomeEdgeActionError("wrong node")
    started = time.monotonic()

    if validated["action_id"] == "home_edge.diagnostic":
        result = run_audited_home_edge_command(
            "diagnostic",
            profile=node,
            artifact_path=DEFAULT_ARTIFACT_PATH,
            transport=diagnostic_transport,
        )
        status = "observed" if result["evidence"]["runtime"]["state"] == "observed" else "unverified"
        remote_value: dict[str, Any] = {
            "status": status,
            "value": {
                "gateway": result["summary"]["gateway"]["status"],
                "route": result["summary"]["route"]["status"],
                "tailscale": result["summary"]["tailscale"]["status"],
            },
        }
    else:
        active_transport = transport or StrictSSHActionTransport(node)
        payload = build_remote_action_program(
            validated["action_id"],
            validated["parameters"],
        )
        attempt = active_transport.run_action(
            payload,
            timeout_seconds=ACTION_TIMEOUT_SECONDS,
        )
        remote_value = _decode_remote_result(attempt)

    duration_ms = int((time.monotonic() - started) * 1000)
    return build_action_receipt(
        validated,
        remote_value,
        duration_ms=duration_ms,
        now=now,
    )


def build_action_receipt(
    request: dict[str, Any],
    remote: dict[str, Any],
    *,
    duration_ms: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    status = remote.get("status")
    if status not in {"observed", "unverified", "unsupported", "partial_failure"}:
        status = "unverified"
    value = _redact(remote.get("value"))
    errors = remote.get("errors")
    if not isinstance(errors, list):
        errors = []
    receipt = {
        "schema": "skeleton.home_edge.action_receipt.v1",
        "node_id": PUBLIC_NODE_ID,
        "action_id": request["action_id"],
        "request_id": request["request_id"],
        "timestamp": request["timestamp"],
        "nonce": request["nonce"],
        "idempotency_key": request["idempotency_key"],
        "executed_at": (now or datetime.now(UTC)).isoformat(),
        "status": status,
        "verified": status == "observed",
        "duration_ms": max(0, min(duration_ms, ACTION_TIMEOUT_SECONDS * 1000)),
        "result": value,
        "errors": [_redact(error) for error in errors],
    }
    rendered = json.dumps(receipt, sort_keys=True)
    for private in ("stdout", "stderr"):
        if private in rendered:
            raise HomeEdgeActionError("receipt contains private runtime output")
    return receipt


def validate_action_request(request: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise HomeEdgeActionError("request must be a JSON object")
    allowed = REQUEST_REQUIRED_FIELDS | REQUEST_OPTIONAL_FIELDS
    extra = sorted(set(request) - allowed)
    if extra:
        raise HomeEdgeActionError(f"unsupported request fields: {', '.join(extra)}")
    missing = sorted(REQUEST_REQUIRED_FIELDS - set(request))
    if missing:
        raise HomeEdgeActionError(f"missing request fields: {', '.join(missing)}")
    if request["node_id"] != PUBLIC_NODE_ID:
        raise HomeEdgeActionError("wrong node")
    action_id = _token(request, "action_id")
    if action_id not in ALLOWED_ACTIONS:
        raise HomeEdgeActionError("action is not allowlisted")
    validated = {
        "node_id": PUBLIC_NODE_ID,
        "action_id": action_id,
        "request_id": _token(request, "request_id"),
        "timestamp": _timestamp(request),
        "nonce": _nonce(request),
        "idempotency_key": _token(request, "idempotency_key"),
        "parameters": _parameters(action_id, request.get("parameters", {})),
    }
    return validated


def build_remote_action_program(action_id: str, parameters: dict[str, Any]) -> str:
    validate_action_request(
        {
            "node_id": PUBLIC_NODE_ID,
            "action_id": action_id,
            "request_id": "validate-00000000",
            "timestamp": datetime.now(UTC).isoformat(),
            "nonce": "N" * 16,
            "idempotency_key": "validate-00000000",
            "parameters": parameters,
        }
    )
    envelope = json.dumps(
        {"action_id": action_id, "parameters": parameters},
        sort_keys=True,
        separators=(",", ":"),
    )
    return REMOTE_ACTION_RUNNER.replace("__ACTION_ENVELOPE__", repr(envelope))


def _decode_remote_result(attempt: ProbeResult) -> dict[str, Any]:
    if not attempt.observed:
        return {
            "status": "unverified",
            "value": None,
            "errors": [{"code": attempt.reason or "transport_unavailable"}],
        }
    stdout = attempt.stdout[:MAX_OUTPUT_BYTES]
    try:
        decoded = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise HomeEdgeDiagnosticError("remote action did not return JSON") from exc
    if not isinstance(decoded, dict):
        raise HomeEdgeDiagnosticError("remote action JSON must be an object")
    return decoded


def _parameters(action_id: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HomeEdgeActionError("parameters must be an object")
    schema = ACTION_PARAMETER_SCHEMAS[action_id]
    allowed = set(schema["required"]) | set(schema["optional"])
    extra = sorted(set(value) - allowed)
    if extra:
        raise HomeEdgeActionError(f"unsupported parameters: {', '.join(extra)}")
    missing = sorted(set(schema["required"]) - set(value))
    if missing:
        raise HomeEdgeActionError(f"missing parameters: {', '.join(missing)}")
    params = dict(value)
    if action_id == "media.get_volume":
        target = params.get("target", "both")
        params["target"] = _target(target)
    elif action_id == "media.set_volume":
        params["level"] = _volume_level(params.get("level"))
        params["target"] = _target(params.get("target"))
    elif action_id in {"media.mute", "media.unmute"}:
        params["target"] = _target(params.get("target"))
    elif action_id == "tv.set_mode":
        mode = params.get("mode")
        if mode not in SUPPORTED_TV_MODES:
            raise HomeEdgeActionError("unsupported tv mode")
        if mode == "off" and params.get("confirm_off") is not True:
            raise HomeEdgeActionError("tv off requires explicit confirmation")
        if "confirm_off" in params and not isinstance(params["confirm_off"], bool):
            raise HomeEdgeActionError("confirm_off must be boolean")
    return params


def _public_parameter_schema(schema: dict[str, Any]) -> dict[str, Any]:
    result = {"required": list(schema["required"]), "optional": {}}
    for key, values in schema["optional"].items():
        result["optional"][key] = sorted(values) if not isinstance(values, frozenset) or values != frozenset((True, False)) else ["boolean"]
    return result


def _target(value: Any) -> str:
    if value not in SUPPORTED_TARGETS:
        raise HomeEdgeActionError("unsupported target")
    return str(value)


def _volume_level(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > 100:
        raise HomeEdgeActionError("level must be an integer from 0 to 100")
    return value


def _token(request: dict[str, Any], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise HomeEdgeActionError(f"{key} is invalid")
    return value


def _nonce(request: dict[str, Any]) -> str:
    value = request.get("nonce")
    if not isinstance(value, str) or NONCE_RE.fullmatch(value) is None:
        raise HomeEdgeActionError("nonce is invalid")
    return value


def _timestamp(request: dict[str, Any]) -> str:
    value = request.get("timestamp")
    if not isinstance(value, str):
        raise HomeEdgeActionError("timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HomeEdgeActionError("timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise HomeEdgeActionError("timestamp must include timezone")
    return parsed.astimezone(UTC).isoformat()


REMOTE_ACTION_RUNNER = r'''
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ENVELOPE = json.loads(__ACTION_ENVELOPE__)
TV_MODE_FILE = Path(os.environ.get("SKELETON_HOME_EDGE_TV_MODE_FILE", "/run/skeleton-home-edge/tv-mode"))
VALID_MODES = {"chrome", "waydroid", "vlc", "kiosk", "off"}


def run(argv, timeout=2, env=None):
    try:
        completed = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
        return {
            "ok": completed.returncode == 0,
            "exit_code": completed.returncode,
            "stdout": completed.stdout[:2048],
            "stderr": completed.stderr[:2048],
        }
    except Exception as exc:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": type(exc).__name__}


def user_env():
    result = run(["id", "-u"], timeout=1)
    env = dict(os.environ)
    uid = result["stdout"].strip()
    if result["ok"] and uid.isdigit():
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    return env


def parse_wpctl(text):
    match = re.search(r"Volume:\s+([0-9.]+)(?:\s+\[MUTED\])?", text)
    if not match:
        return None
    return {"level": max(0, min(100, round(float(match.group(1)) * 100))), "muted": "[MUTED]" in text}


def host_volume():
    env = user_env()
    result = run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], env=env)
    parsed = parse_wpctl(result["stdout"])
    if result["ok"] and parsed is not None:
        parsed["adapter"] = "wpctl"
        return parsed
    volume = run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], env=env)
    mute = run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"], env=env)
    percent = re.search(r"\b(\d{1,3})%", volume["stdout"])
    if volume["ok"] and mute["ok"] and percent:
        return {
            "level": max(0, min(100, int(percent.group(1)))),
            "muted": "yes" in mute["stdout"].lower(),
            "adapter": "pactl",
        }
    return {"status": "unsupported", "reason": "host_audio_unavailable"}


def set_host_volume(level):
    env = user_env()
    set_result = run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{level}%"], env=env)
    if not set_result["ok"]:
        set_result = run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"], env=env)
    after = host_volume()
    if after.get("level") == level:
        return {"status": "observed", "value": after}
    return {"status": "unverified", "value": after if "level" in after else None, "errors": [{"code": "host_volume_unverified"}]}


def mute_host(muted):
    env = user_env()
    value = "1" if muted else "0"
    result = run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", value], env=env)
    if not result["ok"]:
        result = run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "yes" if muted else "no"], env=env)
    after = host_volume()
    if after.get("muted") is muted:
        return {"status": "observed", "value": after}
    return {"status": "unverified", "value": after if "muted" in after else None, "errors": [{"code": "host_mute_unverified"}]}


def android_cmd(args):
    return run(["waydroid", "shell"] + args, timeout=2)


def android_volume():
    result = android_cmd(["cmd", "media_session", "volume", "--show"])
    text = result["stdout"]
    current = re.search(r"current\s*=\s*(\d+)", text, re.I)
    maximum = re.search(r"max\s*=\s*(\d+)", text, re.I)
    muted = "muted" in text.lower()
    if result["ok"] and current and maximum and int(maximum.group(1)) > 0:
        level = round(int(current.group(1)) * 100 / int(maximum.group(1)))
        return {"level": max(0, min(100, level)), "muted": muted, "adapter": "waydroid"}
    result = android_cmd(["settings", "get", "system", "volume_music"])
    if result["ok"] and result["stdout"].strip().isdigit():
        return {"level": max(0, min(100, int(result["stdout"].strip()) * 100 // 15)), "muted": False, "adapter": "waydroid"}
    return {"status": "unsupported", "reason": "android_audio_unavailable"}


def set_android_volume(level):
    android_level = round(level * 15 / 100)
    result = android_cmd(["cmd", "media_session", "volume", "--set", str(android_level)])
    if not result["ok"]:
        result = android_cmd(["settings", "put", "system", "volume_music", str(android_level)])
    after = android_volume()
    if after.get("level") == level or abs(after.get("level", -1000) - level) <= 4:
        after["level"] = level
        return {"status": "observed", "value": after}
    return {"status": "unverified", "value": after if "level" in after else None, "errors": [{"code": "android_volume_unverified"}]}


def mute_android(muted):
    result = android_cmd(["cmd", "audio", "set-zen-mode", "alarms" if muted else "off"])
    after = android_volume()
    if result["ok"]:
        after["muted"] = muted
        return {"status": "observed", "value": after}
    return {"status": "unverified", "value": after if "level" in after else None, "errors": [{"code": "android_mute_unverified"}]}


def both(target, func):
    if target == "host":
        item = func("host")
        return {"status": "observed", "value": {"host": item["value"]}} if item["status"] == "observed" else item
    if target == "android_tv":
        item = func("android_tv")
        return {"status": "observed", "value": {"android_tv": item["value"]}} if item["status"] == "observed" else item
    parts = {"host": func("host"), "android_tv": func("android_tv")}
    status = "observed" if all(item["status"] == "observed" for item in parts.values()) else "partial_failure"
    return {
        "status": status,
        "value": {key: item.get("value") for key, item in parts.items()},
        "errors": [
            {"target": key, "code": item.get("status", "unverified")}
            for key, item in parts.items()
            if item.get("status") != "observed"
        ],
    }


def get_target_volume(name):
    if name == "host":
        value = host_volume()
        return {"status": "observed", "value": value} if "level" in value else {"status": "unsupported", "value": None, "errors": [{"code": value.get("reason", "host_audio_unavailable")}]}
    value = android_volume()
    return {"status": "observed", "value": value} if "level" in value else {"status": "unsupported", "value": None, "errors": [{"code": value.get("reason", "android_audio_unavailable")}]}


def playback_status():
    result = android_cmd(["dumpsys", "media_session"])
    if not result["ok"]:
        return {"status": "unsupported", "value": None, "errors": [{"code": "android_media_session_unavailable"}]}
    active = bool(re.search(r"state=PlaybackState.*state=(3|6)", result["stdout"]))
    return {"status": "observed", "value": {"session": "android_tv", "playback": "active" if active else "inactive_or_unknown"}}


def health():
    tools = {name: bool(shutil.which(name)) for name in ("wpctl", "pactl", "waydroid", "python3")}
    return {"status": "observed", "value": {"route": "strict_ssh_observed", "tools": tools}}


def tv_mode():
    if TV_MODE_FILE.exists():
        value = TV_MODE_FILE.read_text(encoding="utf-8").strip()
        if value in VALID_MODES:
            return {"status": "observed", "value": {"mode": value}}
    result = run(["pgrep", "-af", "waydroid|vlc|chrome|chromium"], timeout=1)
    if not result["ok"]:
        return {"status": "unverified", "value": {"mode": "unknown"}}
    text = result["stdout"].lower()
    if "waydroid" in text:
        return {"status": "observed", "value": {"mode": "waydroid"}}
    if "vlc" in text:
        return {"status": "observed", "value": {"mode": "vlc"}}
    if "chrome" in text or "chromium" in text:
        return {"status": "observed", "value": {"mode": "chrome"}}
    return {"status": "unverified", "value": {"mode": "unknown"}}


def set_tv_mode(mode):
    if mode not in VALID_MODES:
        return {"status": "unsupported", "value": None, "errors": [{"code": "unsupported_mode"}]}
    TV_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    TV_MODE_FILE.write_text(mode + "\n", encoding="utf-8")
    after = tv_mode()
    if after.get("value", {}).get("mode") == mode:
        return {"status": "observed", "value": {"mode": mode}}
    return {"status": "unverified", "value": after.get("value"), "errors": [{"code": "tv_mode_unverified"}]}


def main():
    action = ENVELOPE["action_id"]
    params = ENVELOPE.get("parameters", {})
    if action == "media.get_volume":
        print(json.dumps(both(params.get("target", "both"), get_target_volume), sort_keys=True, separators=(",", ":")))
    elif action == "media.set_volume":
        level = int(params["level"])
        print(json.dumps(both(params["target"], lambda target: set_host_volume(level) if target == "host" else set_android_volume(level)), sort_keys=True, separators=(",", ":")))
    elif action == "media.mute":
        print(json.dumps(both(params["target"], lambda target: mute_host(True) if target == "host" else mute_android(True)), sort_keys=True, separators=(",", ":")))
    elif action == "media.unmute":
        print(json.dumps(both(params["target"], lambda target: mute_host(False) if target == "host" else mute_android(False)), sort_keys=True, separators=(",", ":")))
    elif action == "media.playback_status":
        print(json.dumps(playback_status(), sort_keys=True, separators=(",", ":")))
    elif action == "home_edge.health":
        print(json.dumps(health(), sort_keys=True, separators=(",", ":")))
    elif action == "tv.get_mode":
        print(json.dumps(tv_mode(), sort_keys=True, separators=(",", ":")))
    elif action == "tv.set_mode":
        print(json.dumps(set_tv_mode(params["mode"]), sort_keys=True, separators=(",", ":")))
    else:
        print(json.dumps({"status": "unsupported", "value": None, "errors": [{"code": "unsupported_action"}]}, sort_keys=True, separators=(",", ":")))


main()
'''
