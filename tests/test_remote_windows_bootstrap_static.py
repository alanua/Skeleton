from __future__ import annotations

from pathlib import Path


CMD = Path("ops/remote_node/windows/Skeleton-Remote-Audit.cmd")
PS1 = Path("ops/remote_node/windows/bootstrap.ps1")


def test_one_click_cmd_downloads_public_bootstrap_and_elevates_only_powershell() -> None:
    text = CMD.read_text(encoding="utf-8")

    assert (
        "raw.githubusercontent.com/alanua/Skeleton/"
        "9330095a1849eb5fbe342fdb3caa5bb3b265efd0/ops/remote_node/windows/bootstrap.ps1"
    ) in text
    assert "raw.githubusercontent.com/alanua/Skeleton/main/" not in text
    assert "Start-Process -FilePath powershell.exe -Verb RunAs" in text
    assert "SKELETON_REMOTE_AUDIT_ENROLLMENT_URL" in text
    assert "'-EnrollmentUrl',$url" in text
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


def test_private_https_owner_enrollment_flow_is_supported_without_issue_shell() -> None:
    text = PS1.read_text(encoding="utf-8")

    assert "[string]$EnrollmentUrl" in text
    assert "Assert-HttpsEnrollmentUrl" in text
    assert "$uri.Scheme -ne \"https\"" in text
    assert "$uri.UserInfo" in text
    assert "Invoke-RestMethod -UseBasicParsing -Uri $safeUrl -Method Get" in text
    assert "skeleton.home_edge.remote_windows_audit.owner_enrollment.v1" in text
    assert "Write-EnrollmentReceipt" in text
    assert "Invoke-Expression" not in text
    assert "iex " not in text.lower()


def test_owner_enrollment_schema_has_no_automatic_expiry() -> None:
    text = Path("schemas/remote_windows_owner_enrollment.schema.json").read_text(encoding="utf-8")
    lowered = text.lower()

    assert "expires_at" not in text
    assert "expired" not in lowered
    assert "ttl" not in lowered
    assert "no_automatic_expiry_manual_rotation_or_successful_enrollment_only" in text


def test_admin_support_requires_private_owner_ack_payload() -> None:
    text = PS1.read_text(encoding="utf-8")
    schema = Path("schemas/remote_windows_owner_enrollment.schema.json").read_text(encoding="utf-8")

    assert "support_role" in schema
    assert "admin_support" in schema
    assert "admin_capability_ack" in schema
    assert "owner_approved_admin_capable_support_over_private_tailscale_only" in schema
    assert "$SupportUser = \"skeleton-support\"" in text
    assert "admin_support_owner_ack_required" in text
    assert "Add-LocalGroupMember -Group \"Administrators\" -Member $SupportUser" in text
    assert "PasswordAuthentication no" in text
    assert "AuthorizedKeysFile __PROGRAMDATA__/ssh/skeleton_support_authorized_keys" in text


def test_machine_identity_fingerprint_is_normalized_for_controller_matching() -> None:
    text = PS1.read_text(encoding="utf-8")

    assert "$fingerprintLine = ssh-keygen.exe -l -E sha256 -f \"$key.pub\"" in text
    assert "$fingerprint = ($fingerprintLine -split \"\\s+\")[1]" in text
    assert "$fingerprint -notmatch \"^SHA256:" in text
    assert "machine_identity_ed25519.fingerprint.txt" in text


def test_machine_identity_rotation_and_uninstall_are_idempotent() -> None:
    text = PS1.read_text(encoding="utf-8")

    assert "[switch]$Uninstall" in text
    assert "[switch]$RotateMachineIdentity" in text
    assert "ssh-keygen.exe -t ed25519" in text
    assert "machine_identity_ed25519" in text
    assert "Disable-LocalUser -Name $AuditUser" in text
    assert "Remove-Item -LiteralPath $AuditKeys" in text
