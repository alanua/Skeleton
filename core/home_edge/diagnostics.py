from __future__ import annotations

import ipaddress
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
VISUAL_CAPTURE_TIMEOUT_SECONDS = 300
LAN_INVENTORY_TIMEOUT_SECONDS = 300
LAN_INVENTORY_MAX_ADDRESSES = 256
LAN_INVENTORY_ARTIFACT_ENV = "SKELETON_HOME_EDGE_01_LAN_INVENTORY_ARTIFACT"
LAN_INVENTORY_FIXED_PORTS: dict[str, tuple[int, ...]] = {
    "remote_admin": (22,),
    "web": (80, 443, 8080, 8443),
    "file_services": (139, 445, 2049),
    "home_automation": (1883, 8883, 8123),
    "media": (8096, 9000, 32400),
}
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
        "lan_inventory",
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
        "video_visual_capture",
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
        artifact_target.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return report


def run_home_edge_lan_inventory(
    *,
    profile: HomeEdgeProfile | None = None,
    artifact_path: str | Path | None = None,
    timeout_seconds: int = LAN_INVENTORY_TIMEOUT_SECONDS,
    transport: ProbeTransport | None = None,
) -> dict[str, Any]:
    node = profile or load_home_edge_profile()
    target = _runtime_lan_inventory_artifact_path(artifact_path)
    _validate_private_runtime_artifact_target(target)
    active_transport = transport or OpenSSHTransport(node)
    attempt = active_transport.run_probe(
        LAN_INVENTORY_REMOTE_PROBE,
        timeout_seconds=timeout_seconds,
    )
    if not attempt.observed:
        return {
            "schema": "skeleton.home_edge.lan_inventory.public.v1",
            "node_id": PUBLIC_NODE_ID,
            "action_id": "lan_inventory",
            "status": "unverified",
            "evidence": attempt.public_evidence(),
            "aggregate": {
                "device_count": 0,
                "responsive_count": 0,
                "service_category_counts": {},
                "gateway_presence": "unverified",
                "risk_flags": [attempt.reason or "runtime_unavailable"],
            },
        }
    try:
        decoded = json.loads(attempt.stdout)
    except json.JSONDecodeError as exc:
        raise HomeEdgeDiagnosticError("LAN inventory did not return JSON") from exc
    if not isinstance(decoded, dict):
        raise HomeEdgeDiagnosticError("LAN inventory JSON must be an object")
    aggregate = _validate_lan_inventory_aggregate(decoded.get("aggregate"))
    private_report = {
        "schema": "skeleton.home_edge.lan_inventory.private.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "node_id": PUBLIC_NODE_ID,
        "privacy": "local_private",
        "details": decoded,
    }
    _write_private_runtime_json(target, private_report)
    return {
        "schema": "skeleton.home_edge.lan_inventory.public.v1",
        "node_id": PUBLIC_NODE_ID,
        "action_id": "lan_inventory",
        "status": "observed" if not aggregate["risk_flags"] else "review_required",
        "evidence": attempt.public_evidence(),
        "aggregate": aggregate,
    }


def _runtime_lan_inventory_artifact_path(
    artifact_path: str | Path | None,
) -> Path:
    value = artifact_path
    if value is None:
        env_value = os.environ.get(LAN_INVENTORY_ARTIFACT_ENV, "").strip()
        if env_value:
            value = env_value
    if value is None:
        raise HomeEdgeDiagnosticError(
            "LAN inventory requires an explicit private runtime artifact path"
        )
    return Path(value).expanduser()


def _validate_private_runtime_artifact_target(artifact_path: Path) -> None:
    resolved = artifact_path.resolve(strict=False)
    root = ROOT.resolve(strict=False)
    if resolved == root or _path_is_relative_to(resolved, root):
        raise HomeEdgeDiagnosticError(
            "LAN inventory artifact target must be outside the public repository"
        )


def _write_private_runtime_json(target: Path, payload: dict[str, Any]) -> None:
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if os.name != "posix":
        target.write_text(rendered, encoding="utf-8")
        return

    target.parent.chmod(0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(target, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(rendered)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    target.chmod(0o600)


def _validate_lan_inventory_aggregate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HomeEdgeDiagnosticError("LAN inventory aggregate is missing")
    expected = {
        "device_count",
        "responsive_count",
        "service_category_counts",
        "gateway_presence",
        "risk_flags",
    }
    if set(value) != expected:
        raise HomeEdgeDiagnosticError("LAN inventory aggregate schema mismatch")
    device_count = value.get("device_count")
    responsive_count = value.get("responsive_count")
    category_counts = value.get("service_category_counts")
    gateway_presence = value.get("gateway_presence")
    risk_flags = value.get("risk_flags")
    if (
        not isinstance(device_count, int)
        or isinstance(device_count, bool)
        or device_count < 0
        or device_count > LAN_INVENTORY_MAX_ADDRESSES
        or not isinstance(responsive_count, int)
        or isinstance(responsive_count, bool)
        or responsive_count < 0
        or responsive_count > device_count
        or not isinstance(category_counts, dict)
        or not all(
            isinstance(key, str)
            and key in LAN_INVENTORY_FIXED_PORTS
            and isinstance(count, int)
            and not isinstance(count, bool)
            and 0 <= count <= device_count
            for key, count in category_counts.items()
        )
        or gateway_presence not in {"present", "not_observed", "unverified"}
        or not isinstance(risk_flags, list)
        or not all(
            isinstance(flag, str)
            and 1 <= len(flag) <= 80
            and re.fullmatch(r"[a-z0-9_]+", flag) is not None
            for flag in risk_flags
        )
    ):
        raise HomeEdgeDiagnosticError("LAN inventory aggregate values are invalid")
    return {
        "device_count": device_count,
        "responsive_count": responsive_count,
        "service_category_counts": dict(sorted(category_counts.items())),
        "gateway_presence": gateway_presence,
        "risk_flags": sorted(set(risk_flags)),
    }


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

    if command == "lan_inventory":
        return run_home_edge_lan_inventory(
            profile=node,
            artifact_path=artifact_path,
            transport=transport,
        )
    if command == "gateway_capabilities":
        return gateway_contract()
    if command == "prepare_runtime_bootstrap":
        return prepared_runtime_bootstrap()
    if command == "video_visual_capture":
        return run_home_edge_visual_capture(
            profile=node,
            transport=transport,
        )

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


def run_home_edge_visual_capture(
    *,
    profile: HomeEdgeProfile | None = None,
    transport: ProbeTransport | None = None,
    timeout_seconds: int = VISUAL_CAPTURE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    node = profile or load_home_edge_profile()
    active_transport = transport or OpenSSHTransport(node)
    attempt = active_transport.run_probe(
        VISUAL_CAPTURE_REMOTE_PROBE,
        timeout_seconds=timeout_seconds,
    )
    if not attempt.observed:
        return _validate_visual_capture_public_receipt(
            {
                "schema": "skeleton.home_edge.visual_capture.receipt.v1",
                "action_id": "home_edge_visual_capture_tick",
                "task_ref": "none",
                "status": "FAILED_RETRYABLE",
                "frame_count": 0,
                "manifest_hash": None,
                "capture_mode": "background",
                "reason_codes": [attempt.reason or "remote_runtime_unavailable"],
                "retryable": True,
                "human_review_required": False,
                "stale": False,
            }
        )
    try:
        decoded = json.loads(attempt.stdout)
    except json.JSONDecodeError as exc:
        raise HomeEdgeDiagnosticError("visual capture did not return JSON") from exc
    return _validate_visual_capture_public_receipt(decoded)


def _validate_visual_capture_public_receipt(value: Any) -> dict[str, Any]:
    from .visual_capture import RECEIPT_SCHEMA, TERMINAL_STATUSES

    if not isinstance(value, dict):
        raise HomeEdgeDiagnosticError("visual capture receipt JSON must be an object")
    expected = {
        "schema",
        "action_id",
        "task_ref",
        "status",
        "frame_count",
        "manifest_hash",
        "capture_mode",
        "reason_codes",
        "retryable",
        "human_review_required",
        "stale",
    }
    if set(value) != expected:
        raise HomeEdgeDiagnosticError("visual capture receipt schema mismatch")
    status = value.get("status")
    allowed_statuses = set(TERMINAL_STATUSES) | {"QUEUED"}
    reasons = value.get("reason_codes")
    manifest_hash = value.get("manifest_hash")
    if (
        value.get("schema") != RECEIPT_SCHEMA
        or not isinstance(value.get("action_id"), str)
        or not isinstance(value.get("task_ref"), str)
        or status not in allowed_statuses
        or not isinstance(value.get("frame_count"), int)
        or isinstance(value.get("frame_count"), bool)
        or value.get("frame_count") < 0
        or value.get("capture_mode") not in {"background", "visible_kiosk"}
        or not isinstance(reasons, list)
        or not all(
            isinstance(reason, str)
            and re.fullmatch(r"[a-z0-9_]{1,80}", reason) is not None
            for reason in reasons
        )
        or not isinstance(value.get("retryable"), bool)
        or not isinstance(value.get("human_review_required"), bool)
        or not isinstance(value.get("stale"), bool)
        or (
            manifest_hash is not None
            and (
                not isinstance(manifest_hash, str)
                or re.fullmatch(r"[0-9a-f]{64}", manifest_hash) is None
            )
        )
    ):
        raise HomeEdgeDiagnosticError("visual capture receipt values are invalid")
    rendered = json.dumps(value, sort_keys=True)
    blocked = ("youtube", "http://", "https://", "/", "\\", "profile", "stdout", "stderr")
    if any(marker in rendered.lower() for marker in blocked):
        raise HomeEdgeDiagnosticError("visual capture receipt leaked private data")
    return {
        "schema": RECEIPT_SCHEMA,
        "action_id": value["action_id"],
        "task_ref": value["task_ref"],
        "status": status,
        "frame_count": value["frame_count"],
        "manifest_hash": manifest_hash,
        "capture_mode": value["capture_mode"],
        "reason_codes": sorted(set(reasons)),
        "retryable": value["retryable"],
        "human_review_required": value["human_review_required"],
        "stale": value["stale"],
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
    registered = {
        "state": "registered",
        "internet_path": "default_gateway",
        "gateway_connectivity_hardware": "integrated_modem_expected",
        "gateway_modem_internals": "not_observed_by_home_edge",
        "attached_usb_modem": "optional",
        "health_requirement": False,
    }
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
            "status": "optional_not_attached",
            "evidence": "observed",
            "registered_expectation": registered,
            "observed": {"state": "observed", "present": False},
        }
    modem = _dig(remote, "modemmanager", "modem")
    ports = _dig(remote, "modemmanager", "ports")
    modem_data = modem if isinstance(modem, dict) else {}
    port_data = ports if isinstance(ports, dict) else {}
    return {
        "status": "optional_attached",
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
    modem_observed = modem.get("observed") if isinstance(modem, dict) else None
    observed = modem_observed if isinstance(modem_observed, dict) else {}
    return {
        "node": str(artifact.get("node", {}).get("node_id", PUBLIC_NODE_ID)),
        "gateway_status": str(gateway.get("status", "unverified")),
        "route_status": str(route.get("status", "unverified")),
        "tailscale_status": str(tailscale.get("status", "unverified")),
        "modem_status": str(modem.get("status", "unverified")),
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
    "network": {"default_route": default_route(), "route_to_controller": False},
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


VISUAL_CAPTURE_REMOTE_PROBE = r'''
from __future__ import annotations

import json
import re
import sys

from core.home_edge.visual_capture import RECEIPT_SCHEMA, TERMINAL_STATUSES, process_one_visual_capture_job

EXPECTED = {
    "schema",
    "action_id",
    "task_ref",
    "status",
    "frame_count",
    "manifest_hash",
    "capture_mode",
    "reason_codes",
    "retryable",
    "human_review_required",
    "stale",
}


def validate(value):
    if not isinstance(value, dict) or set(value) != EXPECTED:
        raise ValueError("visual_capture_receipt_schema_mismatch")
    reasons = value.get("reason_codes")
    manifest_hash = value.get("manifest_hash")
    if (
        value.get("schema") != RECEIPT_SCHEMA
        or value.get("status") not in (set(TERMINAL_STATUSES) | {"QUEUED"})
        or not isinstance(value.get("action_id"), str)
        or not isinstance(value.get("task_ref"), str)
        or not isinstance(value.get("frame_count"), int)
        or isinstance(value.get("frame_count"), bool)
        or value.get("frame_count") < 0
        or value.get("capture_mode") not in {"background", "visible_kiosk"}
        or not isinstance(reasons, list)
        or not all(isinstance(reason, str) and re.fullmatch(r"[a-z0-9_]{1,80}", reason) for reason in reasons)
        or not isinstance(value.get("retryable"), bool)
        or not isinstance(value.get("human_review_required"), bool)
        or not isinstance(value.get("stale"), bool)
        or (manifest_hash is not None and (not isinstance(manifest_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", manifest_hash)))
    ):
        raise ValueError("visual_capture_receipt_values_invalid")
    rendered = json.dumps(value, sort_keys=True)
    if any(marker in rendered.lower() for marker in ("youtube", "http://", "https://", "/", "\\", "profile", "stdout", "stderr")):
        raise ValueError("visual_capture_receipt_leaked_private_data")
    return value


try:
    print(json.dumps(validate(process_one_visual_capture_job()), sort_keys=True, separators=(",", ":")))
except Exception as exc:
    print(json.dumps({"status": "blocked", "reason": type(exc).__name__}, sort_keys=True), file=sys.stderr)
    raise
'''


LAN_INVENTORY_REMOTE_PROBE = r'''
from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import shutil
import socket
import subprocess

MAX_ADDRESSES = 256
PING_WORKERS = 16
CONNECT_WORKERS = 16
PRIVATE_RANGES = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
SERVICE_PORTS = {
    "remote_admin": (22,),
    "web": (80, 443, 8080, 8443),
    "file_services": (139, 445, 2049),
    "home_automation": (1883, 8883, 8123),
    "media": (8096, 9000, 32400),
}


def run(cmd, timeout=10):
    try:
        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        return completed.returncode, completed.stdout.strip()
    except Exception:
        return None, ""


def first_json(cmd):
    code, output = run(cmd)
    if code != 0:
        return None
    try:
        return json.loads(output)
    except Exception:
        return None


def route_metric(route):
    metric = route.get("metric", 0)
    return metric if isinstance(metric, int) and not isinstance(metric, bool) else 2**31


def observed_network():
    routes = first_json(["ip", "-j", "route", "show", "default"])
    candidates = [
        route
        for route in routes
        if isinstance(route, dict)
        and isinstance(route.get("dev"), str)
        and route.get("dev")
    ] if isinstance(routes, list) else []
    if not candidates:
        raise ValueError("default_route_unavailable")
    route = min(candidates, key=route_metric)
    interface = route["dev"]
    gateway = route.get("gateway")
    route_source = route.get("prefsrc") or route.get("src")

    addresses = first_json(["ip", "-j", "addr", "show", "dev", interface])
    if not isinstance(addresses, list):
        raise ValueError("primary_address_unavailable")

    address_candidates = []
    for entry in addresses:
        for info in entry.get("addr_info", []) if isinstance(entry, dict) else []:
            if not isinstance(info, dict) or info.get("family") != "inet":
                continue
            local = info.get("local")
            prefixlen = info.get("prefixlen")
            if not isinstance(local, str) or not isinstance(prefixlen, int):
                continue
            try:
                interface_address = ipaddress.ip_interface(f"{local}/{prefixlen}")
            except ValueError:
                continue
            network = interface_address.network
            if network.version == 4 and any(
                network.subnet_of(private_range) for private_range in PRIVATE_RANGES
            ):
                address_candidates.append((local, network))

    if not address_candidates:
        raise ValueError("primary_ipv4_unavailable")

    selected = None
    if route_source:
        matching = [
            candidate for candidate in address_candidates if candidate[0] == route_source
        ]
        if len(matching) != 1:
            raise ValueError("primary_route_source_unavailable")
        selected = matching[0]
    elif isinstance(gateway, str):
        try:
            parsed_gateway = ipaddress.ip_address(gateway)
        except ValueError as exc:
            raise ValueError("gateway_invalid") from exc
        matching = [
            candidate
            for candidate in address_candidates
            if parsed_gateway.version == 4 and parsed_gateway in candidate[1]
        ]
        if len(matching) != 1:
            raise ValueError("primary_ipv4_ambiguous")
        selected = matching[0]
    elif len(address_candidates) == 1:
        selected = address_candidates[0]
    else:
        raise ValueError("primary_ipv4_ambiguous")

    local, network = selected
    if network.num_addresses > MAX_ADDRESSES:
        raise ValueError("network_larger_than_24")

    gateway_address = None
    if gateway is not None:
        if not isinstance(gateway, str):
            raise ValueError("gateway_invalid")
        try:
            parsed_gateway = ipaddress.ip_address(gateway)
        except ValueError as exc:
            raise ValueError("gateway_invalid") from exc
        if parsed_gateway.version != 4 or parsed_gateway not in network:
            raise ValueError("gateway_outside_primary_network")
        gateway_address = str(parsed_gateway)

    return interface, local, gateway_address, network


def neighbor_records(interface, network):
    rows = first_json(["ip", "-j", "neigh", "show", "dev", interface])
    records = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            address = row.get("dst")
            try:
                parsed = ipaddress.ip_address(address)
            except Exception:
                continue
            if parsed.version != 4 or parsed not in network:
                continue
            records[str(parsed)] = {
                "mac": row.get("lladdr"),
                "neighbor_state": row.get("state"),
            }
    return records


def ping(address):
    if shutil.which("ping") is None:
        return False
    code, _output = run(["ping", "-n", "-c", "1", "-W", "1", address], timeout=2)
    return code == 0


def open_categories(address):
    found = []
    for category, ports in SERVICE_PORTS.items():
        for port in ports:
            try:
                with socket.create_connection((address, port), timeout=0.2):
                    found.append(category)
                    break
            except OSError:
                continue
    return sorted(set(found))


def main():
    risk_flags = []
    try:
        interface, local, gateway, network = observed_network()
    except ValueError as exc:
        print(json.dumps({
            "aggregate": {
                "device_count": 0,
                "responsive_count": 0,
                "service_category_counts": {},
                "gateway_presence": "unverified",
                "risk_flags": [str(exc)],
            },
            "details": {"records": []},
        }, sort_keys=True, separators=(",", ":")))
        return
    candidates = [str(address) for address in network.hosts() if str(address) != local]
    neighbors = neighbor_records(interface, network)
    ping_available = shutil.which("ping") is not None
    if not ping_available:
        risk_flags.append("icmp_unavailable")
    responsive = set()
    if ping_available:
        with concurrent.futures.ThreadPoolExecutor(max_workers=PING_WORKERS) as pool:
            for address, ok in zip(candidates, pool.map(ping, candidates)):
                if ok:
                    responsive.add(address)
    scan_targets = sorted(set(neighbors) | responsive | ({gateway} if gateway else set()))
    services = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONNECT_WORKERS) as pool:
        for address, categories in zip(scan_targets, pool.map(open_categories, scan_targets)):
            services[address] = categories
    active = sorted(
        set(neighbors)
        | responsive
        | {address for address, categories in services.items() if categories}
    )
    records = []
    category_counts = {category: 0 for category in SERVICE_PORTS}
    for address in active:
        neighbor = neighbors.get(address, {})
        categories = services.get(address, [])
        for category in categories:
            category_counts[category] += 1
        records.append({
            "address": address,
            "mac": neighbor.get("mac"),
            "neighbor_state": neighbor.get("neighbor_state"),
            "responsive": address in responsive,
            "service_categories": categories,
            "gateway": address == gateway,
        })
    gateway_present = bool(
        gateway and (gateway in neighbors or gateway in responsive or services.get(gateway))
    )
    print(json.dumps({
        "aggregate": {
            "device_count": len(active),
            "responsive_count": len(responsive),
            "service_category_counts": category_counts,
            "gateway_presence": "present" if gateway_present else "not_observed",
            "risk_flags": sorted(set(risk_flags)),
        },
        "details": {
            "network": str(network),
            "interface": interface,
            "gateway": gateway,
            "records": records,
        },
    }, sort_keys=True, separators=(",", ":")))


main()
'''


__all__ = [
    "AUDITED_COMMANDS",
    "DEFAULT_ARTIFACT_PATH",
    "PRIVATE_ARTIFACT_ENV",
    "LAN_INVENTORY_ARTIFACT_ENV",
    "LAN_INVENTORY_FIXED_PORTS",
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
    "run_home_edge_lan_inventory",
    "run_home_edge_visual_capture",
]
