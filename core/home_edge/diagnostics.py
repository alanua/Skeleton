from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .gateway import gateway_contract, prepared_runtime_bootstrap
from .profile import HomeEdgeProfile, load_home_edge_profile
from .transport import OpenSSHTransport, ProbeTransport


DEFAULT_ARTIFACT_PATH = Path("docs/home_edge/home-edge-01-diagnostic.latest.json")
PROBE_TIMEOUT_SECONDS = 90
SENSITIVE_KEY_RE = re.compile(
    r"(pin|password|secret|token|credential|private[_-]?key|imei|imsi|iccid|phone)",
    re.IGNORECASE,
)


class HomeEdgeDiagnosticError(RuntimeError):
    """Raised when a malformed remote response cannot be interpreted safely."""


def run_home_edge_diagnostic(
    *,
    profile: HomeEdgeProfile | None = None,
    artifact_path: str | Path = DEFAULT_ARTIFACT_PATH,
    timeout_seconds: int = PROBE_TIMEOUT_SECONDS,
    transport: ProbeTransport | None = None,
) -> dict[str, Any]:
    node = profile or load_home_edge_profile()
    active_transport = transport or OpenSSHTransport()
    attempt = active_transport.run_probe(REMOTE_PROBE, timeout_seconds=timeout_seconds)
    remote: dict[str, Any] | None = None
    if attempt.observed:
        try:
            decoded = json.loads(attempt.stdout)
        except json.JSONDecodeError as exc:
            raise HomeEdgeDiagnosticError("remote diagnostic did not return JSON") from exc
        if not isinstance(decoded, dict):
            raise HomeEdgeDiagnosticError("remote diagnostic JSON must be an object")
        remote = _redact(decoded)

    report = build_diagnostic_artifact(
        node,
        remote,
        transport_evidence=attempt.public_evidence(),
    )
    path = Path(artifact_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def build_diagnostic_artifact(
    profile: HomeEdgeProfile,
    remote: dict[str, Any] | None,
    *,
    transport_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime_state = "observed" if isinstance(remote, dict) else "unverified"
    transport_public = transport_evidence or {
        "state": runtime_state,
        "adapter": "injected_test_transport" if runtime_state == "observed" else "unverified",
        "target": f"{profile.target_user}@{profile.tailscale_ip}",
        "reason": None if runtime_state == "observed" else "runtime_not_probed",
    }
    return {
        "schema": "skeleton.home_edge.diagnostic.v2",
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "runner_remote_probe" if runtime_state == "observed" else "repository_registration",
        "evidence": {
            "registration": {
                "state": "registered",
                "source": "config/home_edge/home-edge-01.json",
            },
            "runtime": {
                "state": runtime_state,
                "transport": transport_public,
            },
        },
        "node": {
            "evidence": "registered",
            "node_id": profile.node_id,
            "hostname": profile.hostname,
            "tailscale_ip": profile.tailscale_ip,
            "target_user": profile.target_user,
            "os_expected": profile.os,
            "capabilities": list(profile.capabilities),
        },
        "controller": {
            "evidence": "registered",
            "host": profile.controller_host,
            "tailscale_ip": profile.controller_tailscale_ip,
        },
        "safety": {
            "default_route_policy": "preserve_existing_primary_route",
            "task_model": profile.task_model,
            "risk_lanes": list(profile.risk_lanes),
            "boundaries": list(profile.safety_boundaries),
        },
        "runtime": remote,
        "summary": {
            "status": runtime_state,
            "gateway": {
                "status": "ready" if runtime_state == "observed" else "unverified",
                "evidence": runtime_state,
                "target": {
                    "state": "registered",
                    "value": f"{profile.target_user}@{profile.tailscale_ip}",
                },
                "transport": transport_public,
                "contract": gateway_contract(),
            },
            "route": summarize_route(profile, remote),
            "tailscale": summarize_tailscale(profile, remote),
            "modem": summarize_modem(remote),
            "capability_inventory": summarize_capabilities(profile, remote),
            "next_prepared_action": prepared_runtime_bootstrap(),
        },
    }


def summarize_route(profile: HomeEdgeProfile, remote: dict[str, Any] | None) -> dict[str, Any]:
    expected = {
        "state": "registered",
        "interface": profile.primary_network.get("interface"),
        "gateway": profile.primary_network.get("gateway"),
    }
    route = _dig(remote, "network", "default_route") if isinstance(remote, dict) else None
    if not isinstance(route, dict):
        return {
            "status": "unverified",
            "evidence": "unverified",
            "expected": expected,
            "observed": {"state": "unverified", "interface": None, "gateway": None},
        }
    observed = {
        "state": "observed",
        "interface": route.get("dev"),
        "gateway": route.get("gateway"),
    }
    unchanged = observed["interface"] == expected["interface"] and observed["gateway"] == expected["gateway"]
    return {
        "status": "unchanged" if unchanged else "review_required",
        "evidence": "observed",
        "expected": expected,
        "observed": observed,
    }


def summarize_tailscale(profile: HomeEdgeProfile, remote: dict[str, Any] | None) -> dict[str, Any]:
    expected = {"state": "registered", "ip": profile.tailscale_ip}
    tailscale = _dig(remote, "tailscale") if isinstance(remote, dict) else None
    ips = tailscale.get("self_ips") if isinstance(tailscale, dict) else None
    if not isinstance(ips, list):
        return {
            "status": "unverified",
            "evidence": "unverified",
            "expected": expected,
            "observed": {"state": "unverified", "ips": []},
        }
    return {
        "status": "healthy" if profile.tailscale_ip in ips else "review_required",
        "evidence": "observed",
        "expected": expected,
        "observed": {"state": "observed", "ips": ips},
    }


def summarize_modem(remote: dict[str, Any] | None) -> dict[str, Any]:
    registered = {"state": "registered", "role": "optional_external_or_attached_usb_modem"}
    if not isinstance(remote, dict):
        return {
            "status": "unverified",
            "evidence": "unverified",
            "registered_expectation": registered,
            "observed": None,
        }
    usb = _dig(remote, "hardware", "huawei_e3372")
    present = bool(isinstance(usb, dict) and usb.get("present"))
    if not present:
        return {
            "status": "not_present",
            "evidence": "observed",
            "registered_expectation": registered,
            "observed": {"state": "observed", "present": False},
        }
    modem = _dig(remote, "modemmanager", "modem")
    ports = _dig(remote, "modemmanager", "ports")
    modem_data = modem if isinstance(modem, dict) else {}
    port_data = ports if isinstance(ports, dict) else {}
    return {
        "status": "identified",
        "evidence": "observed",
        "registered_expectation": registered,
        "observed": {
            "state": modem_data.get("state"),
            "present": True,
            "model": modem_data.get("model"),
            "usb_id": usb.get("usb_id"),
            "sim_present": modem_data.get("sim_present"),
            "firmware_revision": modem_data.get("firmware_revision"),
            "radio_capabilities": modem_data.get("radio_capabilities", []),
            "supported_modes": modem_data.get("supported_modes", []),
            "current_modes": modem_data.get("current_modes", []),
            "signal": _dig(remote, "modemmanager", "signal") or {},
            "network_interface": port_data.get("net"),
            "serial_port_count": len(port_data.get("serial", [])) if isinstance(port_data.get("serial"), list) else 0,
            "control_port_present": bool(port_data.get("control")),
            "connection_mode": "ModemManager_NCM",
        },
    }


def summarize_capabilities(profile: HomeEdgeProfile, remote: dict[str, Any] | None) -> dict[str, Any]:
    registered = {"state": "registered", "capabilities": list(profile.capabilities)}
    if not isinstance(remote, dict):
        return {
            "status": "unverified",
            "evidence": "unverified",
            "registered": registered,
            "observed": None,
        }
    observed = _dig(remote, "capability_inventory")
    return {
        "status": "observed",
        "evidence": "observed",
        "registered": registered,
        "observed": {
            "state": "observed",
            "value": observed if isinstance(observed, dict) else {},
        },
    }


def build_operator_report(artifact: dict[str, Any]) -> str:
    summary = artifact.get("summary", {})
    gateway = summary.get("gateway", {}) if isinstance(summary, dict) else {}
    route = summary.get("route", {}) if isinstance(summary, dict) else {}
    tailscale = summary.get("tailscale", {}) if isinstance(summary, dict) else {}
    modem = summary.get("modem", {}) if isinstance(summary, dict) else {}
    return "\n".join(
        (
            "Home edge gateway diagnostic complete.",
            f"node={artifact.get('node', {}).get('node_id', 'home-edge-01')}",
            f"gateway={gateway.get('status', 'unverified')}",
            f"route={route.get('status', 'unverified')}",
            f"tailscale={tailscale.get('status', 'unverified')}",
            f"modem={modem.get('status', 'unverified')}",
            "next_action=runner_transport_bootstrap_prepared_not_executed",
        )
    )


def _dig(data: dict[str, Any] | None, *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if SENSITIVE_KEY_RE.search(str(key)) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


REMOTE_PROBE = r'''
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone

SENSITIVE_KEYS = re.compile(r"(pin|password|secret|token|credential|private[_-]?key|imei|imsi|iccid|phone)", re.I)


def run(cmd, timeout=10):
    try:
        completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, check=False)
        return {"ok": completed.returncode == 0, "exit_code": completed.returncode, "stdout": completed.stdout.strip(), "stderr": completed.stderr.strip()}
    except Exception as exc:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": type(exc).__name__}


def redact(value):
    if isinstance(value, dict):
        return {key: "[redacted]" if SENSITIVE_KEYS.search(str(key)) else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def first_json(cmd):
    result = run(cmd)
    if not result["ok"]:
        return None
    try:
        return json.loads(result["stdout"])
    except Exception:
        return None


def default_route():
    routes = first_json(["ip", "-j", "route", "show", "default"])
    if isinstance(routes, list) and routes:
        route = routes[0]
        return {"dev": route.get("dev"), "gateway": route.get("gateway"), "metric": route.get("metric")}
    text = run(["ip", "route", "show", "default"])["stdout"]
    match = re.search(r"default via (?P<gateway>\S+) dev (?P<dev>\S+)", text)
    return {"dev": match.group("dev") if match else None, "gateway": match.group("gateway") if match else None}


def tailscale_status():
    data = first_json(["tailscale", "status", "--json"])
    self_data = data.get("Self") if isinstance(data, dict) else {}
    return {"self_ips": list((self_data or {}).get("TailscaleIPs") or []), "json_available": isinstance(data, dict)}


def tool_inventory():
    tools = (
        "python3", "git", "gh", "docker", "podman", "ansible", "node", "npm",
        "platformio", "pio", "esptool.py", "ffmpeg", "nmcli", "mmcli", "lsusb",
        "ip", "tailscale", "ssh", "curl", "jq", "systemctl", "google-chrome-stable",
        "google-chrome", "chromium", "chromium-browser", "pactl", "pipewire", "waydroid",
        "mosquitto_pub",
    )
    return {tool: {"present": bool(shutil.which(tool))} for tool in tools}


def service_inventory():
    result = {}
    for name in ("NetworkManager.service", "ModemManager.service", "tailscaled.service", "docker.service", "ssh.service"):
        state = run(["systemctl", "is-active", name], timeout=5)
        result[name] = state["stdout"] or "unknown"
    return result


def browser_inventory():
    names = ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser")
    executables = {name: bool(shutil.which(name)) for name in names}
    process = run(["pgrep", "-fc", "chrome|chromium"], timeout=5)
    try:
        count = int(process["stdout"])
    except Exception:
        count = 0
    home = os.path.expanduser("~")
    locks = sum(len(glob.glob(pattern)) for pattern in (
        os.path.join(home, ".config", "google-chrome", "Singleton*"),
        os.path.join(home, ".config", "chromium", "Singleton*"),
    ))
    return {"executables": executables, "process_count": count, "profile_lock_count": locks, "session_type": os.environ.get("XDG_SESSION_TYPE")}


def hardware_inventory():
    result = run(["lsusb"], timeout=10)
    lines = [line for line in result["stdout"].splitlines() if line.strip()]
    present = any("12d1:1506" in line for line in lines)
    return {"usb_device_count": len(lines), "huawei_e3372": {"present": present, "usb_id": "12d1:1506" if present else None}}


def modem_inventory():
    listing = run(["mmcli", "-L"], timeout=10)
    present = "/Modem/0" in listing["stdout"]
    data = first_json(["mmcli", "-m", "0", "-J"]) if present else None
    signal = first_json(["mmcli", "-m", "0", "--signal-get", "-J"]) if present else None
    generic = {}
    if isinstance(data, dict):
        root = data.get("modem") or data.get("Modem") or data
        generic = root.get("generic", {}) if isinstance(root, dict) else {}
        generic = generic if isinstance(generic, dict) else {}
    modem = {
        "model": generic.get("model"),
        "firmware_revision": generic.get("revision"),
        "state": generic.get("state"),
        "sim_present": bool(generic.get("sim")) if generic.get("sim") else "unknown",
        "radio_capabilities": generic.get("supported-capabilities") or generic.get("current-capabilities") or [],
        "supported_modes": generic.get("supported-modes") or [],
        "current_modes": generic.get("current-modes") or [],
    }
    return {
        "present": present,
        "modem": redact(modem),
        "signal": redact(signal or {}),
        "ports": {
            "serial": sorted(glob.glob("/dev/ttyUSB*")),
            "control": next(iter(sorted(glob.glob("/dev/cdc-wdm*"))), None),
            "net": next((os.path.basename(item) for item in sorted(glob.glob("/sys/class/net/wwx*"))), None),
        },
    }


tools = tool_inventory()
browser = browser_inventory()
hardware = hardware_inventory()
disk = shutil.disk_usage("/")
containers = {"available": tools["docker"]["present"], "running_count": None}
if containers["available"]:
    result = run(["docker", "ps", "-q"], timeout=10)
    containers["running_count"] = len([line for line in result["stdout"].splitlines() if line.strip()]) if result["ok"] else None
report = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "system": {
        "hostname": run(["hostname"])["stdout"],
        "kernel": run(["uname", "-r"])["stdout"],
        "memory": run(["sh", "-c", "grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree):' /proc/meminfo"])["stdout"].splitlines(),
        "root_disk": {"total_bytes": disk.total, "used_bytes": disk.used, "free_bytes": disk.free},
    },
    "network": {"default_route": default_route(), "route_to_controller": run(["ip", "route", "get", "100.69.215.63"], timeout=5)["ok"]},
    "tailscale": tailscale_status(),
    "tools": tools,
    "services": service_inventory(),
    "containers": containers,
    "browser": browser,
    "media": {"ffmpeg": tools["ffmpeg"]["present"], "pipewire": tools["pipewire"]["present"], "pactl": tools["pactl"]["present"], "display_present": bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))},
    "home_automation": {"docker_available": tools["docker"]["present"], "python_available": tools["python3"]["present"], "node_available": tools["node"]["present"], "mqtt_cli_available": tools["mosquitto_pub"]["present"]},
    "hardware": hardware,
    "modemmanager": modem_inventory(),
    "capability_inventory": {
        "system": True,
        "network": True,
        "services": True,
        "containers": tools["docker"]["present"] or tools["podman"]["present"],
        "media": tools["ffmpeg"]["present"],
        "browser": any(browser["executables"].values()),
        "hardware": True,
        "home_automation": True,
    },
}
print(json.dumps(redact(report), sort_keys=True, separators=(",", ":")))
'''
