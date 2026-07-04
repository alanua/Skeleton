from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .profile import HOME_EDGE_PUBLIC_PROFILE, HomeEdgeProfile, load_home_edge_profile


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_PATH: Path | None = None
SYNTHETIC_TEMPLATE_ARTIFACT_PATH = Path(
    "docs/home_edge/home-edge-01-diagnostic.latest.json"
)
PRIVATE_ARTIFACT_ENV = "SKELETON_HOME_EDGE_01_DIAGNOSTIC_ARTIFACT"
PROBE_TIMEOUT_SECONDS = 90
SENSITIVE_KEY_RE = re.compile(
    r"(pin|password|secret|token|credential|private[_-]?key|imei|imsi|iccid|phone|"
    r"subscriber|imsi|iccid|hostname|host|user|address|ip|gateway|interface|path)",
    re.IGNORECASE,
)
PUBLIC_NODE_ID = "home-edge-01"

AUDITED_COMMANDS = frozenset(
    (
        "gateway_capabilities",
        "prepare_runtime_bootstrap",
        "diagnostic",
        "identity",
        "system_inventory",
        "network_inventory",
        "service_inventory",
        "container_inventory",
        "media_inventory",
        "browser_diagnostic",
        "hardware_inventory",
        "home_automation_inventory",
        "modem_diagnostic",
        "tool_inventory",
    )
)


class HomeEdgeDiagnosticError(RuntimeError):
    """Raised when a malformed remote response cannot be interpreted safely."""


class HomeEdgeRemoteError(ValueError):
    """Raised when a requested home-edge action is outside the audited gate."""


@dataclass(frozen=True)
class ProbeResult:
    state: str
    adapter: str
    stdout: str = ""
    exit_code: int | None = None
    reason: str | None = None

    @property
    def observed(self) -> bool:
        return self.state == "observed" and self.exit_code == 0

    def public_evidence(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "adapter": self.adapter,
            "target": "private_home_edge",
            "reason": self.reason,
        }


class ProbeTransport(Protocol):
    def run_probe(self, payload: str, *, timeout_seconds: int) -> ProbeResult:
        ...


class OpenSSHTransport:
    adapter_name = "openssh_strict_host_key"

    def __init__(self, profile: HomeEdgeProfile) -> None:
        self.profile = profile

    def run_probe(self, payload: str, *, timeout_seconds: int) -> ProbeResult:
        identity = os.environ.get(self.profile.identity_env, "").strip()
        known_hosts = os.environ.get(self.profile.known_hosts_env, "").strip()
        if not identity or not known_hosts:
            return ProbeResult(
                state="unverified",
                adapter=self.adapter_name,
                reason="strict_ssh_runtime_env_missing",
            )
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts}",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ServerAliveInterval=10",
            "-o",
            "ServerAliveCountMax=3",
            "-i",
            identity,
            f"{self.profile.target_user}@{self.profile.tailscale_ip}",
            "python3",
            "-",
        ]
        try:
            completed = subprocess.run(
                command,
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except TimeoutError:
            raise
        except Exception as exc:
            return ProbeResult(
                state="unverified",
                adapter=self.adapter_name,
                reason=type(exc).__name__,
            )
        if completed.returncode != 0:
            return ProbeResult(
                state="unverified",
                adapter=self.adapter_name,
                stdout=completed.stdout,
                exit_code=completed.returncode,
                reason="strict_ssh_probe_failed",
            )
        return ProbeResult(
            state="observed",
            adapter=self.adapter_name,
            stdout=completed.stdout,
            exit_code=completed.returncode,
        )


def run_home_edge_diagnostic(
    *,
    profile: HomeEdgeProfile | None = None,
    artifact_path: str | Path | None = DEFAULT_ARTIFACT_PATH,
    timeout_seconds: int = PROBE_TIMEOUT_SECONDS,
    transport: ProbeTransport | None = None,
) -> dict[str, Any]:
    node = profile or load_home_edge_profile()
    artifact_target = _runtime_artifact_path(artifact_path)
    _validate_runtime_artifact_target(node, artifact_target)
    active_transport = transport or OpenSSHTransport(node)
    attempt = active_transport.run_probe(REMOTE_PROBE, timeout_seconds=timeout_seconds)
    remote: dict[str, Any] | None = None
    if attempt.observed:
        try:
            decoded = json.loads(attempt.stdout)
        except json.JSONDecodeError as exc:
            raise HomeEdgeDiagnosticError("remote diagnostic did not return JSON") from exc
        if not isinstance(decoded, dict):
            raise HomeEdgeDiagnosticError("remote diagnostic JSON must be an object")
        remote = decoded

    report = build_diagnostic_artifact(
        node,
        remote,
        transport_evidence=attempt.public_evidence(),
    )
    if artifact_target is not None:
        artifact_target.parent.mkdir(parents=True, exist_ok=True)
        persisted_report = _private_runtime_artifact(report, remote)
        if _path_is_relative_to(
            artifact_target.resolve(strict=False), ROOT.resolve(strict=False)
        ):
            persisted_report = report
        artifact_target.write_text(
            json.dumps(
                persisted_report,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    return report


def run_audited_home_edge_command(
    command: str,
    *,
    profile: HomeEdgeProfile | None = None,
    artifact_path: str | Path | None = DEFAULT_ARTIFACT_PATH,
    transport: ProbeTransport | None = None,
) -> dict[str, Any]:
    if command not in AUDITED_COMMANDS:
        raise HomeEdgeRemoteError(f"home-edge action is not allowlisted: {command}")
    node = profile or load_home_edge_profile()

    if command == "gateway_capabilities":
        return gateway_contract()
    if command == "prepare_runtime_bootstrap":
        return prepared_runtime_bootstrap()

    artifact = run_home_edge_diagnostic(
        profile=node,
        artifact_path=artifact_path,
        transport=transport,
    )
    if command == "diagnostic":
        return artifact
    if command == "identity":
        return {
            "schema": "skeleton.home_edge.identity.v2",
            "node": artifact["node"],
            "controller": artifact["controller"],
            "runtime_evidence": artifact["evidence"]["runtime"],
            "gateway": artifact["summary"]["gateway"],
        }

    runtime = artifact.get("runtime")
    runtime_data = runtime if isinstance(runtime, dict) else {}
    domain_statuses = runtime_data.get("domain_statuses")
    domains = domain_statuses if isinstance(domain_statuses, dict) else {}
    projections: dict[str, tuple[str, Any]] = {
        "system_inventory": ("system", domains.get("system", {"status": "unverified"})),
        "network_inventory": ("network", artifact["summary"]["network"]),
        "service_inventory": ("services", domains.get("services", {"status": "unverified"})),
        "container_inventory": ("containers", domains.get("containers", {"status": "unverified"})),
        "media_inventory": ("media", domains.get("media", {"status": "unverified"})),
        "browser_diagnostic": ("browser", domains.get("browser", {"status": "unverified"})),
        "hardware_inventory": ("hardware", artifact["summary"]["hardware"]),
        "home_automation_inventory": (
            "home_automation",
            domains.get("home_automation", {"status": "unverified"}),
        ),
        "modem_diagnostic": ("modem", artifact["summary"]["modem"]),
        "tool_inventory": ("tools", domains.get("tools", {"status": "unverified"})),
    }
    domain, value = projections[command]
    evidence_state = artifact["evidence"]["runtime"]["state"]
    return {
        "schema": f"skeleton.home_edge.{command}.v1",
        "node_id": PUBLIC_NODE_ID,
        "action_id": command,
        "domain": domain,
        "evidence": evidence_state,
        "status": "observed" if evidence_state == "observed" else "unverified",
        "value": value if evidence_state == "observed" else None,
        "transport": artifact["evidence"]["runtime"]["transport"],
    }


def gateway_contract() -> dict[str, Any]:
    return {
        "schema": "skeleton.home_edge.gateway_contract.v1",
        "transport": "openssh_over_tailscale_ip",
        "host_key_policy": "strict",
        "command_source": "fixed_typed_allowlist",
        "actions": sorted(AUDITED_COMMANDS),
        "risk_lanes": ["read_only", "approved_mutation", "destructive_manual"],
    }


def prepared_runtime_bootstrap() -> dict[str, Any]:
    return {
        "schema": "skeleton.home_edge.prepared_runtime_bootstrap.v1",
        "state": "prepared_not_executed",
        "transport": "openssh_over_tailscale_ip",
        "host_key_policy": "strict",
        "credential_source": "runner_private_environment",
        "allowed_runtime_mutation": False,
    }


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
        "target": "private_home_edge",
        "reason": None if runtime_state == "observed" else "runtime_not_probed",
    }
    return {
        "schema": "skeleton.home_edge.diagnostic.v2",
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "runner_remote_probe" if runtime_state == "observed" else "repository_registration",
        "evidence": {
            "registration": {
                "state": "registered",
                "source": "synthetic_public_template",
            },
            "runtime": {
                "state": runtime_state,
                "transport": _redact(transport_public),
            },
        },
        "node": {
            "evidence": "registered",
            "node_id": PUBLIC_NODE_ID,
            "hostname": "synthetic-home-edge",
            "network": "private",
            "target_user": "private",
            "os_expected": profile.os,
            "capabilities": list(profile.capabilities),
        },
        "controller": {
            "evidence": "registered",
            "host": "private-controller",
            "network": "private",
        },
        "safety": {
            "default_route_policy": "preserve_existing_primary_route",
            "task_model": profile.task_model,
            "risk_lanes": list(profile.risk_lanes),
            "boundaries": list(profile.safety_boundaries),
        },
        "runtime": _public_runtime(remote),
        "summary": {
            "status": runtime_state,
            "gateway": {
                "status": "ready" if runtime_state == "observed" else "unverified",
                "evidence": runtime_state,
                "target": {"state": "registered", "value": "private_home_edge"},
                "transport": _redact(transport_public),
                "contract": gateway_contract(),
            },
            "route": summarize_route(profile, remote),
            "tailscale": summarize_tailscale(profile, remote),
            "modem": summarize_modem(remote),
            "network": summarize_network(remote),
            "connected_devices": summarize_connected_devices(remote),
            "gateway_presence": summarize_gateway_presence(remote),
            "connectivity_hardware": summarize_connectivity_hardware(remote),
            "hardware": summarize_hardware(remote),
            "capability_inventory": summarize_capabilities(profile, remote),
            "next_prepared_action": prepared_runtime_bootstrap(),
        },
    }


def summarize_route(profile: HomeEdgeProfile, remote: dict[str, Any] | None) -> dict[str, Any]:
    route = _dig(remote, "network", "default_route") if isinstance(remote, dict) else None
    if not isinstance(route, dict):
        return {
            "status": "unverified",
            "evidence": "unverified",
            "expected": {"state": "registered_private"},
            "observed": {"state": "unverified"},
        }
    unchanged = route.get("dev") == profile.primary_network.get("interface") and route.get(
        "gateway"
    ) == profile.primary_network.get("gateway")
    return {
        "status": "unchanged" if unchanged else "review_required",
        "evidence": "observed",
        "expected": {"state": "registered_private"},
        "observed": {"state": "observed"},
    }


def summarize_tailscale(profile: HomeEdgeProfile, remote: dict[str, Any] | None) -> dict[str, Any]:
    tailscale = _dig(remote, "tailscale") if isinstance(remote, dict) else None
    ips = tailscale.get("self_ips") if isinstance(tailscale, dict) else None
    if not isinstance(ips, list):
        return {
            "status": "unverified",
            "evidence": "unverified",
            "expected": {"state": "registered_private"},
            "observed": {"state": "unverified"},
        }
    return {
        "status": "healthy" if profile.tailscale_ip in ips else "review_required",
        "evidence": "observed",
        "expected": {"state": "registered_private"},
        "observed": {"state": "observed", "ip_count": len(ips)},
    }


def summarize_network(remote: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "status": "observed" if isinstance(remote, dict) else "unverified",
        "route": "aggregate_only",
        "tailscale": "aggregate_only",
        "connected_devices": "aggregate_only",
        "gateway_presence": "aggregate_only",
        "connectivity_hardware": "aggregate_only",
    }


def summarize_connected_devices(remote: dict[str, Any] | None) -> dict[str, Any]:
    devices = (
        _dig(remote, "network", "connected_devices")
        if isinstance(remote, dict)
        else None
    )
    if not isinstance(devices, dict):
        return {
            "status": "unverified",
            "evidence": "unverified",
            "observed": {"state": "unverified"},
        }
    count = devices.get("count")
    return {
        "status": "observed",
        "evidence": "observed",
        "observed": {
            "state": "observed",
            "count": count if isinstance(count, int) else 0,
        },
    }


def summarize_gateway_presence(remote: dict[str, Any] | None) -> dict[str, Any]:
    gateway = (
        _dig(remote, "network", "gateway_presence")
        if isinstance(remote, dict)
        else None
    )
    if not isinstance(gateway, dict):
        return {
            "status": "unverified",
            "evidence": "unverified",
            "observed": {"state": "unverified"},
        }
    present = bool(gateway.get("present"))
    return {
        "status": "present" if present else "not_present",
        "evidence": "observed",
        "observed": {
            "state": "observed",
            "present": present,
            "neighbor_state": str(gateway.get("neighbor_state") or "unknown"),
        },
    }


def summarize_connectivity_hardware(remote: dict[str, Any] | None) -> dict[str, Any]:
    hardware = (
        _dig(remote, "hardware", "connectivity")
        if isinstance(remote, dict)
        else None
    )
    if not isinstance(hardware, dict):
        return {
            "status": "unverified",
            "evidence": "unverified",
            "observed": {"state": "unverified"},
        }
    present = bool(hardware.get("present"))
    interface_count = hardware.get("interface_count")
    return {
        "status": "present" if present else "not_present",
        "evidence": "observed",
        "observed": {
            "state": "observed",
            "present": present,
            "interface_count": interface_count if isinstance(interface_count, int) else 0,
            "default_route_interface_present": bool(
                hardware.get("default_route_interface_present")
            ),
            "usb_modem_present": bool(hardware.get("usb_modem_present")),
        },
    }


def summarize_hardware(remote: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(remote, dict):
        return {"status": "unverified", "evidence": "unverified"}
    usb = _dig(remote, "hardware", "huawei_e3372")
    return {
        "status": "observed",
        "evidence": "observed",
        "optional_usb_modem_present": bool(isinstance(usb, dict) and usb.get("present")),
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
            "sim_present": bool(modem_data.get("sim_present")),
            "radio_capability_count": len(modem_data.get("radio_capabilities", []))
            if isinstance(modem_data.get("radio_capabilities"), list)
            else 0,
            "supported_mode_count": len(modem_data.get("supported_modes", []))
            if isinstance(modem_data.get("supported_modes"), list)
            else 0,
            "current_mode_count": len(modem_data.get("current_modes", []))
            if isinstance(modem_data.get("current_modes"), list)
            else 0,
            "serial_port_count": len(port_data.get("serial", []))
            if isinstance(port_data.get("serial"), list)
            else 0,
            "control_port_present": bool(port_data.get("control")),
            "network_interface_present": bool(port_data.get("net")),
            "connection_mode": "ModemManager_NCM",
        },
    }


def summarize_capabilities(profile: HomeEdgeProfile, remote: dict[str, Any] | None) -> dict[str, Any]:
    registered = {"state": "registered", "capability_count": len(profile.capabilities)}
    if not isinstance(remote, dict):
        return {
            "status": "unverified",
            "evidence": "unverified",
            "registered": registered,
            "observed": None,
        }
    observed = _dig(remote, "capability_inventory")
    observed_count = (
        sum(1 for value in observed.values() if value)
        if isinstance(observed, dict)
        else 0
    )
    return {
        "status": "observed",
        "evidence": "observed",
        "registered": registered,
        "observed": {"state": "observed", "available_count": observed_count},
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
            f"node={artifact.get('node', {}).get('node_id', PUBLIC_NODE_ID)}",
            f"gateway={gateway.get('status', 'unverified')}",
            f"route={route.get('status', 'unverified')}",
            f"tailscale={tailscale.get('status', 'unverified')}",
            f"modem={modem.get('status', 'unverified')}",
            "runtime_details=private",
            "next_action=runner_transport_bootstrap_prepared_not_executed",
        )
    )


def compact_status(artifact: dict[str, Any]) -> dict[str, str]:
    summary = artifact.get("summary", {})
    gateway = summary.get("gateway", {}) if isinstance(summary, dict) else {}
    modem = summary.get("modem", {}) if isinstance(summary, dict) else {}
    route = summary.get("route", {}) if isinstance(summary, dict) else {}
    tailscale = summary.get("tailscale", {}) if isinstance(summary, dict) else {}
    connected_devices = (
        summary.get("connected_devices", {}) if isinstance(summary, dict) else {}
    )
    gateway_presence = (
        summary.get("gateway_presence", {}) if isinstance(summary, dict) else {}
    )
    connectivity_hardware = (
        summary.get("connectivity_hardware", {}) if isinstance(summary, dict) else {}
    )
    modem_observed = modem.get("observed") if isinstance(modem, dict) else None
    observed = modem_observed if isinstance(modem_observed, dict) else {}
    device_observed = (
        connected_devices.get("observed") if isinstance(connected_devices, dict) else None
    )
    device_data = device_observed if isinstance(device_observed, dict) else {}
    return {
        "node": str(artifact.get("node", {}).get("node_id", PUBLIC_NODE_ID)),
        "gateway_status": str(gateway.get("status", "unverified")),
        "route_status": str(route.get("status", "unverified")),
        "tailscale_status": str(tailscale.get("status", "unverified")),
        "modem_status": str(modem.get("status", "unverified")),
        "connected_device_count": str(device_data.get("count", "unverified")),
        "gateway_presence": str(gateway_presence.get("status", "unverified")),
        "connectivity_hardware": str(
            connectivity_hardware.get("status", "unverified")
        ),
        "modem_lock_state": str(observed.get("state", "unverified")).replace(" ", "_"),
        "connection_mode": str(observed.get("connection_mode", "unverified")),
    }


def dumps_public_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def _runtime_artifact_path(artifact_path: str | Path | None) -> Path | None:
    value = artifact_path
    env_value = os.environ.get(PRIVATE_ARTIFACT_ENV, "").strip()
    if value is None and env_value:
        value = env_value
    return Path(value).expanduser() if value is not None else None


def _validate_runtime_artifact_target(
    profile: HomeEdgeProfile, artifact_path: Path | None
) -> None:
    if artifact_path is None:
        return
    resolved = artifact_path.resolve(strict=False)
    root = ROOT.resolve(strict=False)
    if resolved == root or _path_is_relative_to(resolved, root):
        if profile.source != "synthetic_template":
            raise HomeEdgeDiagnosticError(
                "home-edge runtime artifact target must be outside the public repository"
            )
        synthetic_target = (ROOT / SYNTHETIC_TEMPLATE_ARTIFACT_PATH).resolve(
            strict=False
        )
        if resolved != synthetic_target:
            raise HomeEdgeDiagnosticError(
                "template identity may only write the synthetic diagnostic template"
            )


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _domain_status(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "unverified"}
    return {"status": "observed", "field_count": len(value)}


def _tool_status(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "unverified"}
    present = 0
    for details in value.values():
        if isinstance(details, dict) and details.get("present"):
            present += 1
    return {"status": "observed", "tool_count": len(value), "present_count": present}


def _public_runtime(remote: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(remote, dict):
        return None
    return {
        "state": "observed",
        "domains": sorted(str(key) for key in remote),
        "domain_statuses": {
            str(key): _domain_status(value) for key, value in sorted(remote.items())
        },
    }


def _private_runtime_artifact(
    public_artifact: dict[str, Any], remote: dict[str, Any] | None
) -> dict[str, Any]:
    artifact = dict(public_artifact)
    if isinstance(remote, dict):
        artifact["private_runtime"] = {
            "schema": "skeleton.home_edge.private_runtime.v1",
            "privacy": "private_runtime_artifact_only",
            "details": _redact(remote),
        }
    return artifact


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
    if isinstance(value, str):
        if value in HOME_EDGE_PUBLIC_PROFILE.private_values:
            return "[redacted]"
        if "/" in value or re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", value):
            return "[redacted]"
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

SENSITIVE_KEYS = re.compile(r"(pin|password|secret|token|credential|private[_-]?key|imei|imsi|iccid|phone|subscriber)", re.I)


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


def connected_devices(default_gateway=None):
    rows = first_json(["ip", "-j", "neigh", "show"])
    records = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            records.append({
                "address": row.get("dst"),
                "interface": row.get("dev"),
                "hardware_address": row.get("lladdr"),
                "state": row.get("state"),
            })
    else:
        text = run(["ip", "neigh", "show"])["stdout"]
        for line in text.splitlines():
            parts = line.split()
            if not parts:
                continue
            record = {
                "address": parts[0],
                "interface": None,
                "hardware_address": None,
                "state": None,
            }
            if "dev" in parts:
                dev_index = parts.index("dev") + 1
                if dev_index < len(parts):
                    record["interface"] = parts[dev_index]
            if "lladdr" in parts:
                lladdr_index = parts.index("lladdr") + 1
                if lladdr_index < len(parts):
                    record["hardware_address"] = parts[lladdr_index]
            if parts[-1].isupper():
                record["state"] = parts[-1]
            records.append(record)
    state_counts = {}
    gateway_record = None
    for record in records:
        state = str(record.get("state") or "unknown")
        state_counts[state] = state_counts.get(state, 0) + 1
        if default_gateway and record.get("address") == default_gateway:
            gateway_record = record
    return {
        "count": len(records),
        "state_counts": state_counts,
        "gateway_record": gateway_record,
        "records": records,
    }


def connectivity_hardware(default_dev=None, usb_modem_present=False):
    interfaces = []
    for path in sorted(glob.glob("/sys/class/net/*")):
        name = os.path.basename(path)
        if name == "lo":
            continue
        try:
            real_path = os.path.realpath(path)
            with open(os.path.join(path, "operstate"), encoding="utf-8") as handle:
                operstate = handle.read().strip()
            with open(os.path.join(path, "address"), encoding="utf-8") as handle:
                mac = handle.read().strip()
        except Exception:
            real_path = path
            operstate = "unknown"
            mac = None
        interfaces.append({
            "interface": name,
            "operstate": operstate,
            "hardware_address": mac,
            "wireless": os.path.isdir(os.path.join(path, "wireless")),
            "usb_backed": "/usb" in real_path,
            "virtual": real_path.startswith("/sys/devices/virtual/"),
            "default_route_interface": name == default_dev,
        })
    return {
        "present": bool(interfaces or usb_modem_present),
        "interface_count": len(interfaces),
        "default_route_interface_present": any(
            item["default_route_interface"] for item in interfaces
        ),
        "usb_modem_present": bool(usb_modem_present),
        "records": interfaces,
    }


def gateway_presence(route, devices):
    gateway = route.get("gateway") if isinstance(route, dict) else None
    gateway_record = devices.get("gateway_record") if isinstance(devices, dict) else None
    state = gateway_record.get("state") if isinstance(gateway_record, dict) else None
    return {
        "present": bool(gateway),
        "neighbor_observed": isinstance(gateway_record, dict),
        "neighbor_state": state,
    }


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
route = default_route()
devices = connected_devices(route.get("gateway"))
hardware["connectivity"] = connectivity_hardware(
    route.get("dev"), hardware["huawei_e3372"]["present"]
)
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
    "network": {
        "default_route": route,
        "route_to_controller": False,
        "connected_devices": devices,
        "gateway_presence": gateway_presence(route, devices),
    },
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


__all__ = [
    "AUDITED_COMMANDS",
    "DEFAULT_ARTIFACT_PATH",
    "PRIVATE_ARTIFACT_ENV",
    "HomeEdgeDiagnosticError",
    "HomeEdgeRemoteError",
    "OpenSSHTransport",
    "ProbeResult",
    "ProbeTransport",
    "build_diagnostic_artifact",
    "build_operator_report",
    "compact_status",
    "dumps_public_json",
    "gateway_contract",
    "prepared_runtime_bootstrap",
    "run_audited_home_edge_command",
    "run_home_edge_diagnostic",
]
