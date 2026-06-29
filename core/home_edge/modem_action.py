from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Protocol


RESERVED_PROFILE_NAME = "skeleton-home-edge-o2-bounded"
APN_ENV = "SKELETON_HOME_EDGE_01_MODEM_APN"
DEFAULT_APN_DENYLIST = frozenset(("", "internet", "web", "default"))
UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
CONNECTED_STATES = frozenset(("activated", "connected"))


class HomeEdgeModemActionError(RuntimeError):
    """Raised when the bounded modem workflow cannot prove a safe success."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    def run(self, argv: list[str], *, input_text: str | None = None) -> CommandResult:
        ...


class SubprocessCommandRunner:
    def run(self, argv: list[str], *, input_text: str | None = None) -> CommandResult:
        completed = subprocess.run(
            argv,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


@dataclass(frozen=True)
class HomeEdgeModemActionResult:
    status: str
    connection_test: str
    reason: str
    rollback_status: str
    tailscale_before: str
    tailscale_after: str
    route_preserved: bool

    def public_summary(self) -> dict[str, object]:
        return {
            "schema": "skeleton.home_edge.modem_action.public.v1",
            "status": self.status,
            "connection_test": self.connection_test,
            "reason": self.reason,
            "rollback_status": self.rollback_status,
            "tailscale_before": self.tailscale_before,
            "tailscale_after": self.tailscale_after,
            "route_preserved": self.route_preserved,
        }


def run_home_edge_01_modem_probe(
    *,
    runner: CommandRunner | None = None,
    apn: str | None = None,
    profile_name: str = RESERVED_PROFILE_NAME,
) -> HomeEdgeModemActionResult:
    active_runner = runner or SubprocessCommandRunner()
    modem_apn = (apn if apn is not None else os.environ.get(APN_ENV, "")).strip()
    if modem_apn.lower() in DEFAULT_APN_DENYLIST:
        raise HomeEdgeModemActionError("modem_apn_must_be_explicit_non_default")

    tailscale_before = _tailscale_status(active_runner)
    if tailscale_before != "healthy":
        return _blocked("tailscale_preflight_failed", tailscale_before=tailscale_before)

    route_before = _default_route(active_runner)
    if route_before is None:
        return _blocked("primary_route_preflight_failed", tailscale_before=tailscale_before)

    if _profile_name_exists(active_runner, profile_name):
        return _blocked("reserved_profile_name_exists", tailscale_before=tailscale_before)

    created_uuid: str | None = None
    rollback_status = "not_needed"
    try:
        add = active_runner.run(
            [
                "nmcli",
                "connection",
                "add",
                "type",
                "gsm",
                "ifname",
                "*",
                "con-name",
                profile_name,
                "apn",
                modem_apn,
                "connection.autoconnect",
                "no",
                "ipv4.never-default",
                "yes",
                "ipv6.never-default",
                "yes",
            ]
        )
        if add.returncode != 0:
            return _blocked("connection_create_failed", tailscale_before=tailscale_before)
        created_uuid = _created_uuid_from_add(add.stdout)
        if created_uuid is None:
            created_uuid = _lookup_created_uuid(active_runner, profile_name)
        if created_uuid is None:
            return _blocked("created_uuid_lookup_failed", tailscale_before=tailscale_before)

        modify = active_runner.run(
            [
                "nmcli",
                "connection",
                "modify",
                "uuid",
                created_uuid,
                "connection.autoconnect",
                "no",
                "ipv4.never-default",
                "yes",
                "ipv6.never-default",
                "yes",
                "gsm.apn",
                modem_apn,
            ]
        )
        if modify.returncode != 0:
            result = _blocked_with_rollback(
                active_runner,
                created_uuid,
                "connection_modify_failed",
                tailscale_before=tailscale_before,
            )
            rollback_status = result.rollback_status
            return result

        activation = active_runner.run(["nmcli", "connection", "up", "uuid", created_uuid])
        if activation.returncode != 0:
            result = _blocked_with_rollback(
                active_runner,
                created_uuid,
                "connection_activation_failed",
                tailscale_before=tailscale_before,
            )
            rollback_status = result.rollback_status
            return result

        state = _active_state_for_uuid(active_runner, created_uuid)
        if state not in CONNECTED_STATES:
            result = _blocked_with_rollback(
                active_runner,
                created_uuid,
                "active_state_validation_failed",
                tailscale_before=tailscale_before,
            )
            rollback_status = result.rollback_status
            return result

        if not _bounded_device_probe_ok(active_runner, created_uuid):
            result = _blocked_with_rollback(
                active_runner,
                created_uuid,
                "bounded_signal_probe_failed",
                tailscale_before=tailscale_before,
            )
            rollback_status = result.rollback_status
            return result

        route_after = _default_route(active_runner)
        tailscale_after = _tailscale_status(active_runner)
        route_preserved = route_after == route_before
        if not route_preserved:
            result = _blocked_with_rollback(
                active_runner,
                created_uuid,
                "primary_route_changed",
                tailscale_before=tailscale_before,
                tailscale_after=tailscale_after,
                route_preserved=False,
            )
            rollback_status = result.rollback_status
            return result
        if tailscale_after != "healthy":
            result = _blocked_with_rollback(
                active_runner,
                created_uuid,
                "tailscale_recovery_failed",
                tailscale_before=tailscale_before,
                tailscale_after=tailscale_after,
                route_preserved=route_preserved,
            )
            rollback_status = result.rollback_status
            return result

        rollback_status = _delete_created_uuid(active_runner, created_uuid)
        if rollback_status != "deleted":
            return HomeEdgeModemActionResult(
                status="blocked",
                connection_test="blocked",
                reason="rollback_delete_failed",
                rollback_status=rollback_status,
                tailscale_before=tailscale_before,
                tailscale_after=tailscale_after,
                route_preserved=route_preserved,
            )
        return HomeEdgeModemActionResult(
            status="done",
            connection_test="ok",
            reason="bounded_uuid_connection_validated",
            rollback_status=rollback_status,
            tailscale_before=tailscale_before,
            tailscale_after=tailscale_after,
            route_preserved=route_preserved,
        )
    finally:
        if created_uuid is not None and rollback_status == "not_needed":
            _delete_created_uuid(active_runner, created_uuid)


def _blocked(
    reason: str,
    *,
    tailscale_before: str = "unknown",
    tailscale_after: str = "not_checked",
    route_preserved: bool = False,
    rollback_status: str = "not_needed",
) -> HomeEdgeModemActionResult:
    return HomeEdgeModemActionResult(
        status="blocked",
        connection_test="blocked",
        reason=reason,
        rollback_status=rollback_status,
        tailscale_before=tailscale_before,
        tailscale_after=tailscale_after,
        route_preserved=route_preserved,
    )


def _blocked_with_rollback(
    runner: CommandRunner,
    uuid: str,
    reason: str,
    *,
    tailscale_before: str,
    tailscale_after: str = "not_checked",
    route_preserved: bool = False,
) -> HomeEdgeModemActionResult:
    return _blocked(
        reason,
        tailscale_before=tailscale_before,
        tailscale_after=tailscale_after,
        route_preserved=route_preserved,
        rollback_status=_delete_created_uuid(runner, uuid),
    )


def _profile_name_exists(runner: CommandRunner, profile_name: str) -> bool:
    result = runner.run(["nmcli", "-t", "-f", "NAME", "connection", "show", profile_name])
    return result.returncode == 0


def _created_uuid_from_add(output: str) -> str | None:
    match = UUID_RE.search(output)
    return match.group(0).lower() if match else None


def _lookup_created_uuid(runner: CommandRunner, profile_name: str) -> str | None:
    result = runner.run(["nmcli", "-g", "UUID", "connection", "show", profile_name])
    if result.returncode != 0:
        return None
    uuids = [
        line.strip().lower()
        for line in result.stdout.splitlines()
        if UUID_RE.fullmatch(line.strip())
    ]
    return uuids[0] if len(uuids) == 1 else None


def _active_state_for_uuid(runner: CommandRunner, uuid: str) -> str | None:
    result = runner.run(
        ["nmcli", "-t", "-f", "GENERAL.STATE", "connection", "show", "uuid", uuid]
    )
    if result.returncode != 0:
        return None
    return _parse_nmcli_state(result.stdout)


def _bounded_device_probe_ok(runner: CommandRunner, uuid: str) -> bool:
    device_result = runner.run(
        ["nmcli", "-g", "GENERAL.DEVICES", "connection", "show", "uuid", uuid]
    )
    if device_result.returncode != 0:
        return False
    devices = [
        item.strip()
        for item in re.split(r"[\n,]", device_result.stdout)
        if item.strip() and item.strip() != "--"
    ]
    if len(devices) != 1:
        return False
    probe = runner.run(
        [
            "nmcli",
            "-t",
            "-f",
            "GENERAL.STATE,IP4.CONNECTIVITY",
            "device",
            "show",
            devices[0],
        ]
    )
    if probe.returncode != 0:
        return False
    state = _parse_nmcli_state(probe.stdout)
    connectivity = _parse_nmcli_field(probe.stdout, "IP4.CONNECTIVITY")
    return state in CONNECTED_STATES or connectivity in {"full", "limited", "portal"}


def _parse_nmcli_state(output: str) -> str | None:
    value = _parse_nmcli_field(output, "GENERAL.STATE")
    if value is None:
        stripped = output.strip().lower()
        if stripped in CONNECTED_STATES or stripped in {"inactive", "disconnected"}:
            return stripped
        return None
    lowered = value.lower()
    for state in (*CONNECTED_STATES, "inactive", "disconnected"):
        if state in lowered:
            return state
    return None


def _parse_nmcli_field(output: str, field: str) -> str | None:
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip().upper() == field:
            return value.strip().lower()
    return None


def _delete_created_uuid(runner: CommandRunner, uuid: str) -> str:
    result = runner.run(["nmcli", "connection", "delete", "uuid", uuid])
    return "deleted" if result.returncode == 0 else "delete_failed"


def _default_route(runner: CommandRunner) -> str | None:
    result = runner.run(["ip", "route", "show", "default"])
    if result.returncode != 0:
        return None
    route = result.stdout.strip()
    return route or None


def _tailscale_status(runner: CommandRunner) -> str:
    result = runner.run(["tailscale", "status", "--json"])
    if result.returncode != 0:
        return "unhealthy"
    return "healthy"
