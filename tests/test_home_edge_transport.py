from __future__ import annotations

import subprocess
from pathlib import Path

from core.home_edge.transport import (
    FIXED_TARGET_IP,
    FIXED_TARGET_USER,
    SSH_IDENTITY_ENV,
    SSH_KNOWN_HOSTS_ENV,
    OpenSSHTransport,
    TailscaleSSHTransport,
)


def ready_environment(tmp_path: Path) -> dict[str, str]:
    identity = tmp_path / "runner_identity"
    known_hosts = tmp_path / "known_hosts"
    identity.write_text("test-only\n", encoding="utf-8")
    identity.chmod(0o600)
    known_hosts.write_text("fixed-host test-entry\n", encoding="utf-8")
    return {
        SSH_IDENTITY_ENV: str(identity),
        SSH_KNOWN_HOSTS_ENV: str(known_hosts),
    }


def test_openssh_argv_is_exact_and_fixed(tmp_path: Path) -> None:
    environment = ready_environment(tmp_path)
    transport = OpenSSHTransport(environment=environment)

    assert transport.build_probe_argv() == [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=5",
        "-o",
        "ServerAliveCountMax=2",
        "-o",
        f"UserKnownHostsFile={environment[SSH_KNOWN_HOSTS_ENV]}",
        "-i",
        environment[SSH_IDENTITY_ENV],
        f"{FIXED_TARGET_USER}@{FIXED_TARGET_IP}",
        "python3",
        "-",
    ]


def test_strict_host_checking_cannot_be_disabled(tmp_path: Path) -> None:
    argv = OpenSSHTransport(environment=ready_environment(tmp_path)).build_probe_argv()

    assert "StrictHostKeyChecking=yes" in argv
    assert all("StrictHostKeyChecking=no" not in item for item in argv)


def test_connection_fields_ignore_untrusted_values(tmp_path: Path) -> None:
    environment = ready_environment(tmp_path)
    environment.update(
        {
            "identity_file": "/tmp/untrusted-identity",
            "known_hosts_file": "/tmp/untrusted-hosts",
            "host": "203.0.113.1",
            "user": "root",
            "command": "untrusted-command",
        }
    )

    argv = OpenSSHTransport(environment=environment).build_probe_argv()

    assert "/tmp/untrusted-identity" not in argv
    assert "/tmp/untrusted-hosts" not in argv
    assert "203.0.113.1" not in argv
    assert argv[-3:] == [f"{FIXED_TARGET_USER}@{FIXED_TARGET_IP}", "python3", "-"]


def test_missing_material_is_stable_unverified_and_does_not_run() -> None:
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        return subprocess.CompletedProcess(args[0], 0, "{}", "")

    result = OpenSSHTransport(environment={}, subprocess_run=fake_run).run_probe(
        "print('{}')",
        timeout_seconds=10,
    )

    assert called is False
    assert result.state == "unverified"
    assert result.reason == "missing_runner_ssh_material"
    assert result.stdout == ""


def test_tailscale_adapter_is_explicit_and_interactive() -> None:
    transport = TailscaleSSHTransport()

    assert transport.adapter_name == "tailscale_ssh_interactive"
    assert transport.build_probe_argv() == [
        "tailscale",
        "ssh",
        "valertos08@home-edge-01",
        "python3",
        "-",
    ]
