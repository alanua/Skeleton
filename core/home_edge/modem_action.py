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
        "invalid_secret_descriptor",
        "missing_private_secret",
        "modemmanager_unavailable",
        "preflight_recovery_path_unverified",
        "private_unlock_helper_missing",
        "recovery_path_unverified",
        "safety_check_failed",
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
        except TimeoutError:
            raise
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
import subprocess

PACKET = json.loads({packet_json!r})
CREATED_CONNECTION = "skeleton-home-edge-o2-internet-test"


def run(command):
    return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False)


def ok(command):
    return run(command).returncode == 0


def default_route_ok():
    return ok(["ip", "route", "show", "default"])


def tailscale_ok():
    return ok(["tailscale", "status", "--json"])


def rollback(status):
    removed = ok(["nmcli", "-t", "connection", "delete", CREATED_CONNECTION])
    status["rollback"] = "rolled_back" if removed else "unverified"


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
        print(json.dumps(status, sort_keys=True))
        return
    secret_env = descriptor.split(":", 1)[1]
    unlock_secret = os.environ.get(secret_env, "")
    if not unlock_secret:
        status["reason"] = "missing_private_secret"
        print(json.dumps(status, sort_keys=True))
        return
    status["route_before"] = "ok" if default_route_ok() else "blocked"
    status["tailscale_before"] = "ok" if tailscale_ok() else "blocked"
    if status["route_before"] != "ok" or status["tailscale_before"] != "ok":
        status["reason"] = "preflight_recovery_path_unverified"
        print(json.dumps(status, sort_keys=True))
        return
    modem_list = run(["mmcli", "-L"])
    if modem_list.returncode != 0:
        status["reason"] = "modemmanager_unavailable"
        print(json.dumps(status, sort_keys=True))
        return
    # The private runtime must provide a local unlock helper so the PIN is never
    # placed in argv, logs, artifacts, repository files, or public output.
    helper = os.environ.get("SKELETON_HOME_EDGE_01_MODEM_UNLOCK_HELPER", "")
    if not helper:
        status["reason"] = "private_unlock_helper_missing"
        print(json.dumps(status, sort_keys=True))
        return
    unlocked = subprocess.run(
        [helper],
        input=unlock_secret,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    ).returncode == 0
    status["sim_unlock"] = "ok" if unlocked else "blocked"
    if not unlocked:
        rollback(status)
    else:
        created = ok([
            "nmcli",
            "connection",
            "add",
            "type",
            "gsm",
            "ifname",
            "*",
            "con-name",
            CREATED_CONNECTION,
            "apn",
            "internet",
            "connection.autoconnect",
            "no",
            "ipv4.never-default",
            "yes",
            "ipv6.never-default",
            "yes",
        ])
        if created:
            status["rollback"] = "not_needed"
            hardened = ok([
                "nmcli",
                "connection",
                "modify",
                CREATED_CONNECTION,
                "connection.autoconnect",
                "no",
                "ipv4.never-default",
                "yes",
                "ipv6.never-default",
                "yes",
            ])
            status["apn_profile"] = "ok" if hardened else "blocked"
        else:
            status["apn_profile"] = "blocked"
        if status["apn_profile"] != "ok":
            rollback(status)
        else:
            status["connection_test"] = "ok"
            rollback(status)
    status["route_after"] = "ok" if default_route_ok() else "blocked"
    status["tailscale_after"] = "ok" if tailscale_ok() else "blocked"
    if (
        status["sim_unlock"] == "ok"
        and status["apn_profile"] == "ok"
        and status["connection_test"] == "ok"
        and status["rollback"] in {{"not_needed", "rolled_back"}}
        and status["route_after"] == "ok"
        and status["tailscale_after"] == "ok"
    ):
        status["status"] = "ok"
        status["reason"] = "done"
    else:
        status["reason"] = "safety_check_failed"
    print(json.dumps(status, sort_keys=True))


main()
"""
