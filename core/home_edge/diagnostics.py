from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .profile import HomeEdgeProfile, load_home_edge_profile


DEFAULT_ARTIFACT_PATH = Path("docs/home_edge/home-edge-01-diagnostic.latest.json")
PROBE_TIMEOUT_SECONDS = 90


class HomeEdgeDiagnosticError(RuntimeError):
    """Raised when the home-edge diagnostic cannot be completed."""


def run_home_edge_diagnostic(
    *,
    profile: HomeEdgeProfile | None = None,
    artifact_path: str | Path = DEFAULT_ARTIFACT_PATH,
    timeout_seconds: int = PROBE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    node = profile or load_home_edge_profile()
    command = ["tailscale", "ssh", f"{node.target_user}@{node.hostname}", "python3", "-"]
    completed = subprocess.run(
        command,
        input=REMOTE_PROBE,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        raise HomeEdgeDiagnosticError(
            f"tailscale ssh diagnostic failed with exit code {completed.returncode}: "
            f"{_short_error(completed.stderr or completed.stdout)}"
        )

    try:
        remote = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise HomeEdgeDiagnosticError("remote diagnostic did not return JSON") from exc
    if not isinstance(remote, dict):
        raise HomeEdgeDiagnosticError("remote diagnostic JSON must be an object")

    report = build_diagnostic_artifact(node, remote)
    path = Path(artifact_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def build_diagnostic_artifact(profile: HomeEdgeProfile, remote: dict[str, Any]) -> dict[str, Any]:
    modem = summarize_modem(remote)
    route = summarize_route(profile, remote)
    tailscale = summarize_tailscale(profile, remote)
    return {
        "schema": "skeleton.home_edge.diagnostic.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "node": {
            "node_id": profile.node_id,
            "hostname": profile.hostname,
            "tailscale_ip": profile.tailscale_ip,
            "target_user": profile.target_user,
            "os_expected": profile.os,
            "capabilities": list(profile.capabilities),
        },
        "controller": {
            "host": profile.controller_host,
            "tailscale_ip": profile.controller_tailscale_ip,
        },
        "safety": {
            "default_route_policy": "preserve_existing_primary_route",
            "secret_policy": "no_sim_pin_or_private_credentials_in_public_artifacts",
            "boundaries": list(profile.safety_boundaries),
        },
        "remote": remote,
        "summary": {
            "status": "ok",
            "route": route,
            "tailscale": tailscale,
            "modem": modem,
            "huawei_diag_profile": summarize_huawei_diag_profile(remote),
            "next_prepared_action": next_prepared_action(),
        },
    }


def summarize_route(profile: HomeEdgeProfile, remote: dict[str, Any]) -> dict[str, Any]:
    default_route = _dig(remote, "network", "default_route") or {}
    expected = profile.primary_network
    return {
        "status": "unchanged"
        if default_route.get("dev") == expected.get("interface")
        and default_route.get("gateway") == expected.get("gateway")
        else "review_required",
        "expected_interface": expected.get("interface"),
        "expected_gateway": expected.get("gateway"),
        "observed_interface": default_route.get("dev"),
        "observed_gateway": default_route.get("gateway"),
    }


def summarize_tailscale(profile: HomeEdgeProfile, remote: dict[str, Any]) -> dict[str, Any]:
    tailscale = _dig(remote, "tailscale") or {}
    ips = tailscale.get("self_ips") if isinstance(tailscale, dict) else []
    return {
        "status": "healthy" if profile.tailscale_ip in (ips or []) else "review_required",
        "expected_ip": profile.tailscale_ip,
        "observed_ips": ips or [],
    }


def summarize_huawei_diag_profile(remote: dict[str, Any]) -> dict[str, Any]:
    profile = _dig(remote, "network_manager", "huawei_diag")
    if not isinstance(profile, dict) or profile.get("exists") is not True:
        return {"status": "absent_or_ignored", "action": "none"}
    return {
        "status": "ignored",
        "action": "do_not_use_generic_ethernet_profile_for_e3372_ncm_modem",
        "connection_type": profile.get("type"),
    }


def summarize_modem(remote: dict[str, Any]) -> dict[str, Any]:
    usb = _dig(remote, "usb", "huawei_e3372") or {}
    mm = _dig(remote, "modemmanager", "modem") or {}
    ports = _dig(remote, "modemmanager", "ports") or {}
    return {
        "status": "identified" if usb.get("present") else "not_found",
        "model": mm.get("model") or "Huawei E3372",
        "usb_id": "12d1:1506" if usb.get("present") else None,
        "sim_lock_state": mm.get("state") or "unknown",
        "sim_present": mm.get("sim_present", "unknown"),
        "firmware_revision": mm.get("firmware_revision"),
        "radio_capabilities": mm.get("radio_capabilities", []),
        "supported_modes": mm.get("supported_modes", []),
        "current_modes": mm.get("current_modes", []),
        "signal": _dig(remote, "modemmanager", "signal") or {},
        "network_interface": ports.get("net") or "wwx001e101f0000",
        "serial_ports": ports.get("serial") or [],
        "control_port": ports.get("control") or "/dev/cdc-wdm3",
        "connection_mode": "ModemManager_NCM_not_generic_ethernet",
    }


def next_prepared_action() -> dict[str, Any]:
    return {
        "status": "prepared_not_executed",
        "requires_private_secret_route": True,
        "operator_approval_required": True,
        "steps": [
            "unlock SIM through an operator-approved private secret route",
            "create or update O2 APN profile in ModemManager",
            "test antenna placement and signal before gateway migration",
            "plan later MikroTik migration with rollback evidence",
        ],
    }


def build_operator_report(artifact: dict[str, Any]) -> str:
    summary = artifact["summary"]
    modem = summary["modem"]
    route = summary["route"]
    tailscale = summary["tailscale"]
    return "\n".join(
        (
            "Home edge diagnostic complete.",
            f"node={artifact['node']['node_id']} tailscale={tailscale['status']} route={route['status']}",
            (
                f"modem={modem['model']} usb_id={modem['usb_id']} "
                f"sim_lock_state={modem['sim_lock_state']} mode={modem['connection_mode']}"
            ),
            "next_action=private_sim_unlock_o2_apn_signal_test_prepared_not_executed",
        )
    )


def _dig(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _short_error(text: str) -> str:
    return " ".join(text.strip().split())[:500] or "no output"


REMOTE_PROBE = r'''
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone


SENSITIVE_KEYS = re.compile(r"(pin|password|secret|token|credential|imei|imsi|iccid|equipment.identifier|own.numbers|sim.identifier)", re.I)


def run(cmd, timeout=10):
    try:
        completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, check=False)
        return {"ok": completed.returncode == 0, "exit_code": completed.returncode, "stdout": completed.stdout.strip(), "stderr": completed.stderr.strip()}
    except Exception as exc:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}


def redact(value):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if SENSITIVE_KEYS.search(str(key)):
                redacted[key] = "[redacted]"
            else:
                redacted[key] = redact(item)
        return redacted
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


def tool_inventory():
    tools = ("python3", "git", "gh", "codex", "docker", "ansible", "node", "npm", "platformio", "pio", "esptool.py", "ffmpeg", "nmcli", "mmcli", "lsusb", "ip", "tailscale", "ssh", "curl", "jq", "qmicli", "mbimcli", "picocom", "minicom", "avrdude")
    found = {}
    for tool in tools:
        path = shutil.which(tool)
        entry = {"present": bool(path), "path": path}
        if path:
            version = run([tool, "--version"], timeout=5)
            entry["version"] = (version["stdout"] or version["stderr"]).splitlines()[:2]
        found[tool] = entry
    return found


def default_route():
    routes = first_json(["ip", "-j", "route", "show", "default"])
    if isinstance(routes, list) and routes:
        route = routes[0]
        return {"dev": route.get("dev"), "gateway": route.get("gateway"), "raw": route}
    text = run(["ip", "route", "show", "default"])["stdout"]
    match = re.search(r"default via (?P<gateway>\S+) dev (?P<dev>\S+)", text)
    return {"dev": match.group("dev") if match else None, "gateway": match.group("gateway") if match else None, "raw_text": text}


def tailscale_status():
    data = first_json(["tailscale", "status", "--json"])
    ips = []
    if isinstance(data, dict):
        self_data = data.get("Self") or {}
        ips = list(self_data.get("TailscaleIPs") or [])
    return {"self_ips": ips, "json_available": isinstance(data, dict)}


def nmcli_huawei_diag():
    result = run(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "huawei-diag"])
    if not result["ok"]:
        return {"exists": False}
    fields = result["stdout"].split(":")
    return {"exists": True, "name": fields[0] if fields else "huawei-diag", "type": fields[1] if len(fields) > 1 else None, "device": fields[2] if len(fields) > 2 else None}


def modemmanager():
    listing = run(["mmcli", "-L"])
    modem_path = "/org/freedesktop/ModemManager1/Modem/0" if "/Modem/0" in listing["stdout"] else None
    modem_json = first_json(["mmcli", "-m", "0", "-J"]) if modem_path else None
    signal_json = first_json(["mmcli", "-m", "0", "--signal-get", "-J"]) if modem_path else None
    modem = {}
    ports = {"serial": sorted(glob.glob("/dev/ttyUSB*")), "control": next(iter(sorted(glob.glob("/dev/cdc-wdm*"))), None), "net": None}
    for candidate in sorted(glob.glob("/sys/class/net/wwx*")):
        ports["net"] = os.path.basename(candidate)
        break
    if isinstance(modem_json, dict):
        m = modem_json.get("modem") or modem_json.get("Modem") or modem_json
        generic = m.get("generic", {}) if isinstance(m.get("generic"), dict) else {}
        modem = {
            "path": modem_path,
            "manufacturer": generic.get("manufacturer"),
            "model": generic.get("model"),
            "firmware_revision": generic.get("revision"),
            "state": generic.get("state"),
            "sim_present": bool(generic.get("sim")) if generic.get("sim") else "unknown",
            "radio_capabilities": generic.get("supported-capabilities") or generic.get("current-capabilities") or [],
            "supported_modes": generic.get("supported-modes") or [],
            "current_modes": generic.get("current-modes") or [],
        }
        if not modem["state"]:
            text = run(["mmcli", "-m", "0"])["stdout"]
            match = re.search(r"state:\s+'?([^'\n]+)'?", text)
            modem["state"] = match.group(1).strip() if match else None
    return {"listing": redact(listing), "modem": redact(modem), "signal": redact(signal_json or {}), "ports": ports}


def usb():
    lsusb = run(["lsusb"])
    return {"huawei_e3372": {"present": "12d1:1506" in lsusb["stdout"], "usb_id": "12d1:1506" if "12d1:1506" in lsusb["stdout"] else None}, "lsusb": lsusb}


report = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "hostname": run(["hostname"])["stdout"],
    "os_release": run(["bash", "-lc", ". /etc/os-release && printf '%s %s' \"$PRETTY_NAME\" \"$VERSION_CODENAME\""])["stdout"],
    "kernel": run(["uname", "-a"])["stdout"],
    "network": {"default_route": default_route(), "route_to_controller": run(["ip", "route", "get", "100.69.215.63"])},
    "tailscale": tailscale_status(),
    "tools": tool_inventory(),
    "usb": usb(),
    "kernel_modules": {"option": run(["bash", "-lc", "lsmod | awk '$1==\"option\" || $1==\"huawei_cdc_ncm\" || $1==\"usb_storage\" {print $1}'"])},
    "network_manager": {"huawei_diag": nmcli_huawei_diag()},
    "modemmanager": modemmanager(),
}

print(json.dumps(redact(report), sort_keys=True, separators=(",", ":")))
'''
