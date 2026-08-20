from __future__ import annotations

from pathlib import Path


CMD = Path("ops/remote_node/windows/Skeleton-Remote-Audit.cmd")
PS1 = Path("ops/remote_node/windows/bootstrap.ps1")


def test_one_click_cmd_downloads_public_bootstrap_and_elevates_only_powershell() -> None:
    text = CMD.read_text(encoding="utf-8")

    assert "raw.githubusercontent.com/alanua/Skeleton/main/ops/remote_node/windows/bootstrap.ps1" in text
    assert "Start-Process -FilePath powershell.exe -Verb RunAs" in text
    assert "tailscale" not in text.lower()
    assert "ssh-ed25519" not in text
    assert "TOKEN" not in text.upper()


def test_bootstrap_fails_legacy_windows_before_installation() -> None:
    text = PS1.read_text(encoding="utf-8")

    assert "Assert-SupportedWindows" in text
    assert "unsupported_non_x64_windows" in text
    assert "unsupported_legacy_windows" in text
    assert text.index("Assert-SupportedWindows") < text.index("Ensure-Tailscale")
    assert text.index("Assert-SupportedWindows") < text.index("Ensure-OpenSsh")


def test_bootstrap_installs_only_official_tailscale_and_windows_openssh() -> None:
    text = PS1.read_text(encoding="utf-8")

    assert "https://pkgs.tailscale.com/stable/tailscale-setup-latest-amd64.msi" in text
    assert "Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0" in text
    assert "winget" not in text.lower()
    assert "choco" not in text.lower()
    assert "Invoke-Expression" not in text


def test_dedicated_public_key_only_audit_user_and_no_public_internet_ssh() -> None:
    text = PS1.read_text(encoding="utf-8")

    assert "$AuditUser = \"skeleton-audit\"" in text
    assert "PasswordAuthentication no" in text
    assert "KbdInteractiveAuthentication no" in text
    assert "AuthorizedKeysFile __PROGRAMDATA__/ssh/skeleton_audit_authorized_keys" in text
    assert "AllowTcpForwarding no" in text
    assert "ForceCommand powershell.exe -NoProfile -NonInteractive" in text
    assert "Set-NetFirewallRule -Profile Private" in text


def test_private_one_link_can_prestage_controller_public_key_without_public_token() -> None:
    text = PS1.read_text(encoding="utf-8")

    assert "[string]$ControllerPublicKey" in text
    assert "controller_authorized_key.pub" in text
    assert "Read-Host \"Paste the Skeleton controller ssh-ed25519 public key\"" in text
    assert "private_https_link" not in text
    assert "windows_bootstrap_one_link_v1" not in text


def test_machine_identity_rotation_and_uninstall_are_idempotent() -> None:
    text = PS1.read_text(encoding="utf-8")

    assert "[switch]$Uninstall" in text
    assert "[switch]$RotateMachineIdentity" in text
    assert "ssh-keygen.exe -t ed25519" in text
    assert "machine_identity_ed25519" in text
    assert "Disable-LocalUser -Name $AuditUser" in text
    assert "Remove-Item -LiteralPath $AuditKeys" in text
