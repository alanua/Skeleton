from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any, Protocol

from .profile import HomeEdgeProfile, load_home_edge_profile


TASK_ID = "home_edge_01_private_sim_unlock_o2_apn_test"
RUNTIME_APPROVAL_MARKER = "APPROVE_HOME_EDGE_01_PRIVATE_SIM_UNLOCK_O2_APN_TEST"
SECRET_DESCRIPTOR_ENV = "SKELETON_HOME_EDGE_01_MODEM_SIM_UNLOCK_SECRET_DESCRIPTOR"
DEFAULT_SECRET_DESCRIPTOR = "env:SKELETON_HOME_EDGE_01_MODEM_SIM_UNLOCK_PIN"
PROBE_TIMEOUT_SECONDS = 180
PUBLIC_NODE_ID = "home-edge-01"

SAFE_STATUSES = frozenset({"ok", "blocked", "rolled_back", "not_needed", "unverified"})
SAFE_REASONS = frozenset(
    {
        "done",
        "active_state_validation_failed",
        "bounded_signal_probe_failed",
        "connection_activation_failed",
        "connection_create_failed",
        "connection_modify_failed",
        "connection_uuid_generation_failed",
        "invalid_secret_descriptor",
        "missing_private_secret",
        "modemmanager_unavailable",
        "postflight_recovery_path_unverified",
        "preflight_recovery_path_unverified",
        "private_unlock_helper_missing",
        "recovery_path_unverified",
        "reserved_profile_lookup_error",
        "reserved_profile_name_exists",
        "safety_check_failed",
        "transport_timeout",
        "transport_unverified",
        "unverified",
    }
)


class HomeEdgeModemActionError(RuntimeError):
    """Raised when a modem action response cannot be interpreted safely."""


@dataclass(frozen=True)
class ModemActionResult:
    state: str
    adapter: str
    stdout: str = ""
    exit_code: int | None = None
    reason: str | None = None

    @property
    def observed(self) -> bool:
        return self.state == "observed" and self.exit_code == 0


class ModemActionTransport(Protocol):
    def run_action(self, payload: str, *, timeout_seconds: int) -> ModemActionResult:
        ...


class OpenSSHModemActionTransport:
    adapter_name = "openssh_strict_host_key"

    def __init__(self, profile: HomeEdgeProfile) -> None:
        self.profile = profile

    def run_action(self, payload: str, *, timeout_seconds: int) -> ModemActionResult:
        identity = os.environ.get(self.profile.identity_env, "").strip()
        known_hosts = os.environ.get(self.profile.known_hosts_env, "").strip()
        if not identity or not known_hosts:
            return ModemActionResult(
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
        except subprocess.TimeoutExpired:
            return ModemActionResult(
                state="unverified",
                adapter=self.adapter_name,
                reason="transport_timeout",
            )
        except Exception as exc:
            return ModemActionResult(
                state="unverified",
                adapter=self.adapter_name,
                reason=type(exc).__name__,
            )
        if completed.returncode != 0:
            return ModemActionResult(
                state="unverified",
                adapter=self.adapter_name,
                stdout=completed.stdout,
                exit_code=completed.returncode,
                reason="strict_ssh_action_failed",
            )
        return ModemActionResult(
            state="observed",
            adapter=self.adapter_name,
            stdout=completed.stdout,
            exit_code=completed.returncode,
        )


def run_private_sim_unlock_o2_apn_test(
    *,
    runtime_approval_marker: str | None,
    profile: HomeEdgeProfile | None = None,
    secret_descriptor: str | None = None,
    transport: ModemActionTransport | None = None,
    timeout_seconds: int = PROBE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    node = profile or load_home_edge_profile()
    descriptor = secret_descriptor or os.environ.get(
        SECRET_DESCRIPTOR_ENV, DEFAULT_SECRET_DESCRIPTOR
    )
    status = _base_status(node)
    if runtime_approval_marker != RUNTIME_APPROVAL_MARKER:
        return _blocked(status, "missing_runtime_approval")
    if node.is_template_identity or node.source == "synthetic_template":
        return _blocked(status, "private_runtime_profile_required")
    if not _valid_secret_descriptor(descriptor):
        return _blocked(status, "invalid_secret_descriptor")

    active_transport = transport or OpenSSHModemActionTransport(node)
    packet = _action_packet(descriptor)
    attempt = active_transport.run_action(
        _remote_action_payload(packet),
        timeout_seconds=timeout_seconds,
    )
    if not attempt.observed:
        return _blocked(status, attempt.reason or "transport_unverified")
    try:
        decoded = json.loads(attempt.stdout)
    except json.JSONDecodeError as exc:
        raise HomeEdgeModemActionError("remote modem action did not return JSON") from exc
    if not isinstance(decoded, dict):
        raise HomeEdgeModemActionError("remote modem action JSON must be an object")
    return _sanitize_remote_status(status, decoded)


def _base_status(profile: HomeEdgeProfile) -> dict[str, Any]:
    return {
        "schema": "skeleton.home_edge.modem_action.v1",
        "task_id": TASK_ID,
        "node_id": PUBLIC_NODE_ID,
        "target": "private_home_edge",
        "profile_source": profile.source,
        "approval_status": "unverified",
        "secret_source": "private_inherited_descriptor",
        "route_before": "unverified",
        "tailscale_before": "unverified",
        "sim_unlock": "unverified",
        "apn_profile": "unverified",
        "connection_test": "unverified",
        "rollback": "not_needed",
        "route_after": "unverified",
        "tailscale_after": "unverified",
        "status": "blocked",
        "reason": "unverified",
    }


def _blocked(status: dict[str, Any], reason: str) -> dict[str, Any]:
    result = dict(status)
    result["status"] = "blocked"
    result["reason"] = reason
    return result


def _valid_secret_descriptor(value: str) -> bool:
    return (
        value == DEFAULT_SECRET_DESCRIPTOR
        or value.startswith("env:SKELETON_HOME_EDGE_01_MODEM_")
    ) and all(ch.isalnum() or ch in "_:" for ch in value)


def _action_packet(secret_descriptor: str) -> dict[str, Any]:
    return {
        "schema": "skeleton.home_edge.modem_action_request.v1",
        "task_id": TASK_ID,
        "approval_marker": RUNTIME_APPROVAL_MARKER,
        "secret_descriptor": secret_descriptor,
        "modem_vendor": "huawei",
        "sim_unlock": True,
        "apn": {
            "name": "o2-internet-test",
            "apn": "internet",
            "default_route": False,
            "autoconnect": False,
        },
        "safety": {
            "preserve_primary_route": True,
            "preserve_tailscale_recovery": True,
            "rollback_created_session_profile": True,
            "public_output": "aggregate_status_only",
        },
    }


def _sanitize_remote_status(
    base_status: dict[str, Any], remote: dict[str, Any]
) -> dict[str, Any]:
    allowed_keys = {
        "approval_status",
        "route_before",
        "tailscale_before",
        "sim_unlock",
        "apn_profile",
        "connection_test",
        "rollback",
        "route_after",
        "tailscale_after",
        "status",
        "reason",
    }
    result = dict(base_status)
    for key in allowed_keys:
        value = remote.get(key)
        if key == "reason":
            if isinstance(value, str) and value in SAFE_REASONS:
                result[key] = value
            continue
        if isinstance(value, str) and value in SAFE_STATUSES | {"verified"}:
            result[key] = value
    if result["status"] != "ok":
        result["status"] = "blocked"
        if result["rollback"] == "unverified":
            result["rollback"] = "rolled_back"
    if result["route_after"] != "ok" or result["tailscale_after"] != "ok":
        result["status"] = "blocked"
        result["reason"] = "recovery_path_unverified"
    return result


def compact_status(artifact: dict[str, Any]) -> dict[str, str]:
    return {
        "task_id": str(artifact.get("task_id", TASK_ID)),
        "node_id": str(artifact.get("node_id", PUBLIC_NODE_ID)),
        "approval_status": str(artifact.get("approval_status", "unverified")),
        "secret_source": str(artifact.get("secret_source", "private_inherited_descriptor")),
        "route_before": str(artifact.get("route_before", "unverified")),
        "tailscale_before": str(artifact.get("tailscale_before", "unverified")),
        "sim_unlock": str(artifact.get("sim_unlock", "unverified")),
        "apn_profile": str(artifact.get("apn_profile", "unverified")),
        "connection_test": str(artifact.get("connection_test", "unverified")),
        "rollback": str(artifact.get("rollback", "unverified")),
        "route_after": str(artifact.get("route_after", "unverified")),
        "tailscale_after": str(artifact.get("tailscale_after", "unverified")),
        "status": str(artifact.get("status", "blocked")),
        "reason": str(artifact.get("reason", "unverified")),
    }


def _remote_action_payload(packet: dict[str, Any]) -> str:
    packet_json = json.dumps(packet, sort_keys=True)
    return f"""\
from __future__ import annotations

import json
import os
import re
import subprocess
import uuid

PACKET = json.loads({packet_json!r})
CREATED_CONNECTION = "skeleton-home-edge-o2-internet-test"
COMMAND_TIMEOUT_SECONDS = 20
CONNECTED_STATES = {{"activated", "connected"}}


def run(command, *, input_text=None, timeout=COMMAND_TIMEOUT_SECONDS):
    try:
        return subprocess.run(
            command,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None


def ok(command, *, input_text=None):
    result = run(command, input_text=input_text)
    return result is not None and result.returncode == 0


def default_route():
    result = run(["ip", "route", "show", "default"])
    if result is None or result.returncode != 0:
        return None
    route = result.stdout.strip()
    return route or None


def tailscale_ok():
    return ok(["tailscale", "status", "--json"])


def emit(status):
    print(json.dumps(status, sort_keys=True))


def rollback(status, connection_uuid):
    if not connection_uuid:
        status["rollback"] = "not_needed"
        return
    removed = ok(["nmcli", "connection", "delete", "uuid", connection_uuid])
    status["rollback"] = "rolled_back" if removed else "unverified"


def reserved_name_state():
    result = run(["nmcli", "-t", "-f", "NAME", "connection", "show", CREATED_CONNECTION])
    if result is None:
        return "ERROR"
    if result.returncode == 0:
        return "EXISTS"
    if result.returncode == 10:
        return "NOT_FOUND"
    return "ERROR"


def parse_field(output, field):
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip().upper() == field:
            return value.strip().lower()
    return None


def parse_state(output):
    value = parse_field(output, "GENERAL.STATE")
    if value is None:
        stripped = output.strip().lower()
        if stripped in CONNECTED_STATES or stripped in {{"inactive", "disconnected"}}:
            return stripped
        return None
    for state in (*CONNECTED_STATES, "inactive", "disconnected"):
        if state in value:
            return state
    return None


def active_state_for_uuid(connection_uuid):
    result = run([
        "nmcli",
        "-t",
        "-f",
        "GENERAL.STATE",
        "connection",
        "show",
        "uuid",
        connection_uuid,
    ])
    if result is None or result.returncode != 0:
        return None
    return parse_state(result.stdout)


def bounded_device_probe_ok(connection_uuid):
    device_result = run([
        "nmcli",
        "-g",
        "GENERAL.DEVICES",
        "connection",
        "show",
        "uuid",
        connection_uuid,
    ])
    if device_result is None or device_result.returncode != 0:
        return False
    devices = [
        item.strip()
        for item in re.split(r"[\\n,]", device_result.stdout)
        if item.strip() and item.strip() != "--"
    ]
    if len(devices) != 1:
        return False
    probe = run([
        "nmcli",
        "-t",
        "-f",
        "GENERAL.STATE,IP4.CONNECTIVITY",
        "device",
        "show",
        devices[0],
    ])
    if probe is None or probe.returncode != 0:
        return False
    state = parse_state(probe.stdout)
    connectivity = parse_field(probe.stdout, "IP4.CONNECTIVITY")
    return state in CONNECTED_STATES or connectivity in {{"full", "limited", "portal"}}


def block_after_create(status, connection_uuid, reason):
    status["reason"] = reason
    rollback(status, connection_uuid)


def main():
    status = {{
        "approval_status": "verified",
        "route_before": "unverified",
        "tailscale_before": "unverified",
        "sim_unlock": "unverified",
        "apn_profile": "unverified",
        "connection_test": "unverified",
        "rollback": "not_needed",
        "route_after": "unverified",
        "tailscale_after": "unverified",
        "status": "blocked",
        "reason": "unverified",
    }}
    descriptor = PACKET.get("secret_descriptor")
    if not isinstance(descriptor, str) or not descriptor.startswith("env:"):
        status["reason"] = "invalid_secret_descriptor"
        emit(status)
        return
    secret_env = descriptor.split(":", 1)[1]
    unlock_secret = os.environ.get(secret_env, "")
    if not unlock_secret:
        status["reason"] = "missing_private_secret"
        emit(status)
        return
    route_before = default_route()
    status["route_before"] = "ok" if route_before else "blocked"
    status["tailscale_before"] = "ok" if tailscale_ok() else "blocked"
    if status["route_before"] != "ok" or status["tailscale_before"] != "ok":
        status["reason"] = "preflight_recovery_path_unverified"
        emit(status)
        return
    modem_list = run(["mmcli", "-L"])
    if modem_list is None or modem_list.returncode != 0:
        status["reason"] = "modemmanager_unavailable"
        emit(status)
        return
    helper = os.environ.get("SKELETON_HOME_EDGE_01_MODEM_UNLOCK_HELPER", "")
    if not helper:
        status["reason"] = "private_unlock_helper_missing"
        emit(status)
        return
    unlocked = ok([helper], input_text=unlock_secret)
    status["sim_unlock"] = "ok" if unlocked else "blocked"
    if not unlocked:
        status["reason"] = "safety_check_failed"
        emit(status)
        return
    reserved = reserved_name_state()
    if reserved == "EXISTS":
        status["reason"] = "reserved_profile_name_exists"
        emit(status)
        return
    if reserved != "NOT_FOUND":
        status["reason"] = "reserved_profile_lookup_error"
        emit(status)
        return
    connection_uuid = str(uuid.uuid4())
    if not connection_uuid:
        status["reason"] = "connection_uuid_generation_failed"
        emit(status)
        return
    add = ok([
        "nmcli",
        "connection",
        "add",
        "type",
        "gsm",
        "ifname",
        "*",
        "con-name",
        CREATED_CONNECTION,
        "connection.uuid",
        connection_uuid,
        "apn",
        "internet",
        "connection.autoconnect",
        "no",
        "ipv4.never-default",
        "yes",
        "ipv6.never-default",
        "yes",
    ])
    if not add:
        status["apn_profile"] = "blocked"
        status["reason"] = "connection_create_failed"
    else:
        status["rollback"] = "not_needed"
        modified = ok([
            "nmcli",
            "connection",
            "modify",
            "uuid",
            connection_uuid,
            "connection.autoconnect",
            "no",
            "ipv4.never-default",
            "yes",
            "ipv6.never-default",
            "yes",
            "gsm.apn",
            "internet",
        ])
        if not modified:
            status["apn_profile"] = "blocked"
            block_after_create(status, connection_uuid, "connection_modify_failed")
        else:
            status["apn_profile"] = "ok"
            activated = ok(["nmcli", "connection", "up", "uuid", connection_uuid])
            if not activated:
                block_after_create(status, connection_uuid, "connection_activation_failed")
            elif active_state_for_uuid(connection_uuid) not in CONNECTED_STATES:
                block_after_create(status, connection_uuid, "active_state_validation_failed")
            elif not bounded_device_probe_ok(connection_uuid):
                block_after_create(status, connection_uuid, "bounded_signal_probe_failed")
            else:
                status["connection_test"] = "ok"
                rollback(status, connection_uuid)
    route_after = default_route()
    status["route_after"] = "ok" if route_after == route_before else "blocked"
    status["tailscale_after"] = "ok" if tailscale_ok() else "blocked"
    if (
        status["sim_unlock"] == "ok"
        and status["apn_profile"] == "ok"
        and status["connection_test"] == "ok"
        and status["rollback"] == "rolled_back"
        and status["route_after"] == "ok"
        and status["tailscale_after"] == "ok"
    ):
        status["status"] = "ok"
        status["reason"] = "done"
    elif status["reason"] == "unverified":
        status["reason"] = "safety_check_failed"
    emit(status)


main()
"""
