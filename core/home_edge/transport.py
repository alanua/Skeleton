from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Protocol

from .profile import HomeEdgeProfile, load_home_edge_profile


SSH_IDENTITY_ENV = "SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE"
SSH_KNOWN_HOSTS_ENV = "SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE"
FIXED_REMOTE_COMMAND = ("python3", "-")
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10
DEFAULT_SERVER_ALIVE_INTERVAL_SECONDS = 5
DEFAULT_SERVER_ALIVE_COUNT_MAX = 2


class HomeEdgeTransportError(RuntimeError):
    """Raised when the home-edge transport contract cannot be used."""


@dataclass(frozen=True)
class TransportPreflight:
    state: str
    adapter: str
    target: str
    reason: str | None = None
    identity_file: Path | None = field(default=None, repr=False)
    known_hosts_file: Path | None = field(default=None, repr=False)

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    def public_evidence(self) -> dict[str, object]:
        return {
            "state": "registered" if self.ready else "unverified",
            "adapter": self.adapter,
            "target": self.target,
            "identity_env": SSH_IDENTITY_ENV,
            "known_hosts_env": SSH_KNOWN_HOSTS_ENV,
            "strict_host_key_checking": True,
            "batch_mode": True,
            "identities_only": True,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ProbeResult:
    state: str
    adapter: str
    target: str = "unverified"
    stdout: str = field(default="", repr=False)
    stderr: str = field(default="", repr=False)
    exit_code: int | None = None
    reason: str | None = None

    @property
    def observed(self) -> bool:
        return self.state == "observed"

    def public_evidence(self) -> dict[str, object]:
        return {
            "state": self.state,
            "adapter": self.adapter,
            "target": self.target,
            "strict_host_key_checking": self.adapter == "openssh",
            "exit_code": self.exit_code,
            "reason": self.reason,
        }


class ProbeTransport(Protocol):
    adapter_name: str

    def run_probe(self, payload: str, *, timeout_seconds: int) -> ProbeResult:
        ...


@dataclass
class OpenSSHTransport:
    """Runner transport with profile-derived target and strict host verification."""

    profile: HomeEdgeProfile | None = None
    environment: Mapping[str, str] | None = None
    subprocess_run: Callable[..., subprocess.CompletedProcess[str]] = field(default=subprocess.run, repr=False, compare=False)
    adapter_name: str = field(default="openssh", init=False)

    def _profile(self) -> HomeEdgeProfile:
        return self.profile or load_home_edge_profile(environment=self.environment)

    def preflight(self) -> TransportPreflight:
        profile = self._profile()
        environment = self.environment if self.environment is not None else os.environ
        identity_raw = environment.get(SSH_IDENTITY_ENV, "").strip()
        known_hosts_raw = environment.get(SSH_KNOWN_HOSTS_ENV, "").strip()
        if not identity_raw or not known_hosts_raw:
            return TransportPreflight("unverified", self.adapter_name, profile.target, "missing_runner_ssh_material")
        identity_file = Path(identity_raw)
        known_hosts_file = Path(known_hosts_raw)
        if not identity_file.is_file() or not known_hosts_file.is_file():
            return TransportPreflight("unverified", self.adapter_name, profile.target, "runner_ssh_material_not_found")
        if identity_file.stat().st_mode & 0o077:
            return TransportPreflight("unverified", self.adapter_name, profile.target, "runner_identity_permissions_too_open")
        if known_hosts_file.stat().st_size == 0:
            return TransportPreflight("unverified", self.adapter_name, profile.target, "runner_known_hosts_empty")
        return TransportPreflight("ready", self.adapter_name, profile.target, identity_file=identity_file, known_hosts_file=known_hosts_file)

    def build_probe_argv(self) -> list[str]:
        preflight = self.preflight()
        if not preflight.ready or preflight.identity_file is None or preflight.known_hosts_file is None:
            raise HomeEdgeTransportError(preflight.reason or "runner_transport_unverified")
        return [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"ConnectTimeout={DEFAULT_CONNECT_TIMEOUT_SECONDS}",
            "-o",
            f"ServerAliveInterval={DEFAULT_SERVER_ALIVE_INTERVAL_SECONDS}",
            "-o",
            f"ServerAliveCountMax={DEFAULT_SERVER_ALIVE_COUNT_MAX}",
            "-o",
            f"UserKnownHostsFile={preflight.known_hosts_file}",
            "-i",
            str(preflight.identity_file),
            preflight.target,
            *FIXED_REMOTE_COMMAND,
        ]

    def run_probe(self, payload: str, *, timeout_seconds: int) -> ProbeResult:
        preflight = self.preflight()
        if not preflight.ready:
            return ProbeResult("unverified", self.adapter_name, target=preflight.target, reason=preflight.reason)
        try:
            completed = self.subprocess_run(
                self.build_probe_argv(),
                input=payload,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ProbeResult("unverified", self.adapter_name, target=preflight.target, reason="runner_ssh_timeout")
        except OSError:
            return ProbeResult("unverified", self.adapter_name, target=preflight.target, reason="runner_ssh_unavailable")
        if completed.returncode != 0:
            return ProbeResult("unverified", self.adapter_name, target=preflight.target, exit_code=completed.returncode, reason="runner_ssh_probe_failed")
        return ProbeResult("observed", self.adapter_name, target=preflight.target, stdout=completed.stdout, stderr=completed.stderr, exit_code=completed.returncode)


@dataclass
class TailscaleSSHTransport:
    """Optional interactive adapter; never selected by Runner production code."""

    profile: HomeEdgeProfile | None = None
    environment: Mapping[str, str] | None = None
    subprocess_run: Callable[..., subprocess.CompletedProcess[str]] = field(default=subprocess.run, repr=False, compare=False)
    adapter_name: str = field(default="tailscale_ssh_interactive", init=False)

    def _profile(self) -> HomeEdgeProfile:
        return self.profile or load_home_edge_profile(environment=self.environment)

    def build_probe_argv(self) -> list[str]:
        return ["tailscale", "ssh", self._profile().interactive_target, *FIXED_REMOTE_COMMAND]

    def run_probe(self, payload: str, *, timeout_seconds: int) -> ProbeResult:
        target = self._profile().interactive_target
        try:
            completed = self.subprocess_run(
                self.build_probe_argv(),
                input=payload,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            return ProbeResult("unverified", self.adapter_name, target=target, reason="interactive_tailscale_ssh_unavailable")
        if completed.returncode != 0:
            return ProbeResult("unverified", self.adapter_name, target=target, exit_code=completed.returncode, reason="interactive_tailscale_ssh_probe_failed")
        return ProbeResult("observed", self.adapter_name, target=target, stdout=completed.stdout, stderr=completed.stderr, exit_code=completed.returncode)
