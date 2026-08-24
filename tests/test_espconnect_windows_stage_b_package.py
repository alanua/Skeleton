from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "scripts" / "espconnect_windows_stage_b_install.ps1"
SERVE = ROOT / "scripts" / "espconnect_windows_stage_b_serve.ps1"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_installer_is_pinned_verified_plan_first_and_user_local() -> None:
    source = text(INSTALL)
    assert "$Version = 'v1.1.18'" in source
    assert "$ExpectedTagCommit = '77c79a01786881206ad9b3ccbe3db2ddb08f2989'" in source
    assert "thelastoutpostworkshop/ESPConnect" in source
    assert "releases/download/$Version/$AssetName" in source
    assert "$AssetName = 'dist.zip'" in source
    assert "releases/tags/$Version" in source
    assert "git/ref/tags/$Version" in source
    assert "release_asset_sha256_digest_missing" in source
    assert "Get-FileHash -Algorithm SHA256" in source
    assert "release_asset_sha256_mismatch" in source
    assert "release_tag_commit_mismatch" in source
    assert "[switch]$Apply" in source
    assert "'plan_only'" in source
    assert "if (-not $Apply)" in source
    assert "LOCALAPPDATA" in source
    assert "install_root_outside_skeleton_localappdata" in source
    assert "localhost_only = $true" in source
    assert "destructive_operations_enabled = $false" in source
    assert "destructive_operations_enabled_by_installer = $false" in source


def test_installer_has_transactional_activation_and_rollback_copy() -> None:
    source = text(INSTALL)
    assert ".staging-" in source
    assert ".previous" in source
    assert "$OldMoved = $false" in source
    assert "$NewActivated = $false" in source
    assert "Move-Item -LiteralPath $TargetRoot -Destination $BackupRoot" in source
    assert "Move-Item -LiteralPath $StagingRoot -Destination $TargetRoot" in source
    assert "Move-Item -LiteralPath $BackupRoot -Destination $TargetRoot" in source
    assert "rollback_copy_retained" in source
    assert "staging_index_missing" in source
    assert "Copy-Item -LiteralPath (Join-Path $DistRoot '*')" not in source


def test_package_does_not_install_services_or_expose_network_listener() -> None:
    install = text(INSTALL).lower()
    serve = text(SERVE).lower()
    forbidden_installer_tokens = (
        "new-service",
        "sc.exe create",
        "schtasks",
        "register-scheduledtask",
        "start-service",
        "netsh advfirewall",
        "0.0.0.0",
        "write-flash",
        "erase-flash",
        "erase_flash",
        "powershell -enc",
        "invoke-expression",
    )
    for token in forbidden_installer_tokens:
        assert token not in install
    assert "http://127.0.0.1:$port/" in serve
    assert "0.0.0.0" not in serve
    assert "[net.sockets.tcplistener]" in serve
    assert "[net.ipaddress]::loopback" in serve
    assert "httplistener" not in serve
    assert "netsh" not in serve
    assert "http_sys = $false" in serve
    assert "admin_required = $false" in serve
    assert "start-process $url" in serve


def test_static_server_confines_requests_to_install_root() -> None:
    source = text(SERVE)
    assert "[Uri]::UnescapeDataString" in source
    assert "[IO.Path]::GetFullPath" in source
    assert "path_escape" in source
    assert "StartsWith($Root + [IO.Path]::DirectorySeparatorChar" in source
    assert "Cache-Control: no-store" in source
    assert "X-Content-Type-Options: nosniff" in source
    assert "index.html" in source
    assert "@('GET', 'HEAD')" in source
