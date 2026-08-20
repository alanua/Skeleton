#requires -version 5.1
[CmdletBinding()]
param(
  [switch]$Uninstall,
  [switch]$RotateMachineIdentity,
  [string]$EnrollmentUrl,
  [string]$ControllerPublicKey
)

$ErrorActionPreference = "Stop"
$AuditUser = "skeleton-audit"
$Root = Join-Path $env:ProgramData "Skeleton\RemoteAudit"
$IdentityRoot = Join-Path $Root "identity"
$SshRoot = Join-Path $env:ProgramData "ssh"
$AdminKeys = Join-Path $SshRoot "administrators_authorized_keys"
$AuditKeys = Join-Path $SshRoot "skeleton_audit_authorized_keys"
$EnrollmentReceipt = Join-Path $Root "enrollment.json"
$TailscaleMsi = Join-Path $env:TEMP "tailscale-setup.msi"
$TailscaleUrl = "https://pkgs.tailscale.com/stable/tailscale-setup-latest-amd64.msi"
$OwnerEnrollmentSchema = "skeleton.home_edge.remote_windows_audit.owner_enrollment.v1"
$script:OwnerEnrollmentLoaded = $false
$script:OwnerEnrollmentRecord = $null

function Fail($Reason) {
  Write-Error "Skeleton Remote Audit bootstrap blocked: $Reason"
  exit 2
}

function Assert-Admin {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail "administrator_approval_required"
  }
}

function Assert-SupportedWindows {
  if (-not [Environment]::Is64BitOperatingSystem) { Fail "unsupported_non_x64_windows" }
  $os = Get-CimInstance -ClassName Win32_OperatingSystem
  $build = [int]$os.BuildNumber
  if (($os.Caption -notmatch "Windows 10|Windows 11") -or $build -lt 19041) {
    Fail "unsupported_legacy_windows"
  }
}

function Ensure-Directories {
  New-Item -ItemType Directory -Force -Path $Root, $IdentityRoot | Out-Null
  icacls $Root /inheritance:r /grant:r "SYSTEM:(OI)(CI)(F)" "Administrators:(OI)(CI)(F)" | Out-Null
}

function Ensure-Tailscale {
  $svc = Get-Service -Name Tailscale -ErrorAction SilentlyContinue
  if (-not $svc) {
    Invoke-WebRequest -UseBasicParsing -Uri $TailscaleUrl -OutFile $TailscaleMsi
    Start-Process -FilePath msiexec.exe -Wait -ArgumentList @("/i", $TailscaleMsi, "/qn", "/norestart")
  }
  Start-Service -Name Tailscale -ErrorAction SilentlyContinue
  $exe = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
  if (Test-Path -LiteralPath $exe) {
    & $exe up --accept-dns=false
  }
}

function Ensure-OpenSsh {
  $cap = Get-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
  if ($cap.State -ne "Installed") {
    Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
  }
  Set-Service -Name sshd -StartupType Automatic
  Start-Service -Name sshd
}

function Ensure-AuditUser {
  $existing = Get-LocalUser -Name $AuditUser -ErrorAction SilentlyContinue
  if (-not $existing) {
    $password = [Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Minimum 0 -Maximum 256 }))
    $secure = ConvertTo-SecureString $password -AsPlainText -Force
    New-LocalUser -Name $AuditUser -Password $secure -Description "Skeleton public-key-only remote audit account" -PasswordNeverExpires:$true | Out-Null
  }
  net user $AuditUser /active:yes | Out-Null
}

function Ensure-MachineIdentity {
  $key = Join-Path $IdentityRoot "machine_identity_ed25519"
  if ($RotateMachineIdentity -and (Test-Path -LiteralPath $key)) {
    Remove-Item -LiteralPath $key, "$key.pub" -Force -ErrorAction SilentlyContinue
  }
  if (-not (Test-Path -LiteralPath $key)) {
    ssh-keygen.exe -t ed25519 -N "" -C "skeleton-remote-audit-machine-identity" -f $key | Out-Null
  }
  $fingerprintLine = ssh-keygen.exe -l -E sha256 -f "$key.pub"
  $fingerprint = ($fingerprintLine -split "\s+")[1]
  if ($fingerprint -notmatch "^SHA256:[A-Za-z0-9+/=]{20,80}$") {
    Fail "machine_identity_fingerprint_unavailable"
  }
  $fingerprint | Out-File -FilePath (Join-Path $IdentityRoot "machine_identity_ed25519.fingerprint.txt") -Encoding ascii
  icacls $key /inheritance:r /grant:r "SYSTEM:(F)" "Administrators:(F)" | Out-Null
}

function Assert-HttpsEnrollmentUrl($Url) {
  $uri = $null
  if (-not [Uri]::TryCreate($Url, [UriKind]::Absolute, [ref]$uri)) { Fail "enrollment_url_invalid" }
  if ($uri.Scheme -ne "https") { Fail "enrollment_url_must_be_https" }
  if ($uri.UserInfo) { Fail "enrollment_url_must_not_include_credentials" }
  return $uri.AbsoluteUri
}

function Read-OwnerEnrollment {
  if ($script:OwnerEnrollmentLoaded) { return $script:OwnerEnrollmentRecord }
  $script:OwnerEnrollmentLoaded = $true
  if (-not $EnrollmentUrl) { return $null }
  $safeUrl = Assert-HttpsEnrollmentUrl $EnrollmentUrl
  $record = Invoke-RestMethod -UseBasicParsing -Uri $safeUrl -Method Get
  if ($record.schema -ne $OwnerEnrollmentSchema) { Fail "enrollment_schema_mismatch" }
  if ($record.audit_user -and $record.audit_user -ne $AuditUser) { Fail "enrollment_audit_user_mismatch" }
  if ($record.transport -and $record.transport -ne "tailscale_openssh") { Fail "enrollment_transport_mismatch" }
  if (-not $record.controller_public_key) { Fail "controller_public_key_required" }
  if ($record.controller_public_key -notmatch "^ssh-ed25519\s+[A-Za-z0-9+/=]+(\s+.*)?$") {
    Fail "controller_public_key_required"
  }
  $script:OwnerEnrollmentRecord = $record
  return $script:OwnerEnrollmentRecord
}

function Read-ControllerPublicKey {
  if ($ControllerPublicKey) { return $ControllerPublicKey.Trim() }
  $record = Read-OwnerEnrollment
  if ($record) { return $record.controller_public_key.Trim() }
  $candidate = Join-Path $Root "controller_authorized_key.pub"
  if (Test-Path -LiteralPath $candidate) {
    return (Get-Content -LiteralPath $candidate -Raw).Trim()
  }
  $value = Read-Host "Paste the Skeleton controller ssh-ed25519 public key"
  return $value.Trim()
}

function Configure-SshPolicy {
  $publicKey = Read-ControllerPublicKey
  if ($publicKey -notmatch "^ssh-ed25519\s+[A-Za-z0-9+/=]+(\s+.*)?$") {
    Fail "controller_public_key_required"
  }
  $publicKey | Out-File -FilePath $AuditKeys -Encoding ascii -Force
  icacls $AuditKeys /inheritance:r /grant:r "SYSTEM:(F)" "Administrators:(F)" | Out-Null
  $config = Join-Path $SshRoot "sshd_config"
  $block = @"

Match User $AuditUser
    PubkeyAuthentication yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AuthorizedKeysFile __PROGRAMDATA__/ssh/skeleton_audit_authorized_keys
    AllowTcpForwarding no
    X11Forwarding no
    PermitTunnel no
    ForceCommand powershell.exe -NoProfile -NonInteractive
"@
  $existing = if (Test-Path -LiteralPath $config) { Get-Content -LiteralPath $config -Raw } else { "" }
  $clean = $existing -replace "(?ms)\r?\n?Match User skeleton-audit\r?\n.*?(?=\r?\nMatch |\z)", ""
  ($clean.TrimEnd() + $block + "`r`n") | Out-File -FilePath $config -Encoding ascii -Force
  Restart-Service -Name sshd
}

function Write-EnrollmentReceipt {
  $record = Read-OwnerEnrollment
  if (-not $record) { return }
  $publicKeyPath = Join-Path $IdentityRoot "machine_identity_ed25519.pub"
  $fingerprintPath = Join-Path $IdentityRoot "machine_identity_ed25519.fingerprint.txt"
  $receipt = [ordered]@{
    schema = "skeleton.home_edge.remote_audit_enrollment.v1"
    enrollment_id = $record.enrollment_id
    node_id = "home-edge-01"
    audit_user = $AuditUser
    transport = "tailscale_openssh"
    machine_identity_public_key = (Get-Content -LiteralPath $publicKeyPath -Raw).Trim()
    machine_identity_fingerprint = (Get-Content -LiteralPath $fingerprintPath -Raw).Trim()
    registered_at = (Get-Date).ToUniversalTime().ToString("o")
    owner_enrollment_link_sha256 = if ($record.link_sha256) { $record.link_sha256 } else { $null }
    controller_known_hosts_ref = "private-controller-storage"
  }
  $receipt | ConvertTo-Json -Depth 6 | Out-File -FilePath $EnrollmentReceipt -Encoding utf8 -Force
  icacls $EnrollmentReceipt /inheritance:r /grant:r "SYSTEM:(F)" "Administrators:(F)" | Out-Null
}

function Restrict-PublicInternetSsh {
  Get-NetFirewallRule -DisplayName "OpenSSH SSH Server*" -ErrorAction SilentlyContinue |
    Set-NetFirewallRule -Profile Private
}

function Uninstall-SkeletonAudit {
  Remove-Item -LiteralPath $AuditKeys -Force -ErrorAction SilentlyContinue
  $config = Join-Path $SshRoot "sshd_config"
  if (Test-Path -LiteralPath $config) {
    $clean = (Get-Content -LiteralPath $config -Raw) -replace "(?ms)\r?\n?Match User skeleton-audit\r?\n.*?(?=\r?\nMatch |\z)", ""
    $clean.TrimEnd() | Out-File -FilePath $config -Encoding ascii -Force
    Restart-Service -Name sshd -ErrorAction SilentlyContinue
  }
  Disable-LocalUser -Name $AuditUser -ErrorAction SilentlyContinue
  Write-Host "Skeleton audit access revoked. Tailscale and OpenSSH were left installed."
}

Assert-Admin
Assert-SupportedWindows
Ensure-Directories
if ($Uninstall) {
  Uninstall-SkeletonAudit
  exit 0
}
Ensure-Tailscale
Ensure-OpenSsh
Ensure-AuditUser
Ensure-MachineIdentity
Configure-SshPolicy
Write-EnrollmentReceipt
Restrict-PublicInternetSsh
Write-Host "Skeleton Remote Audit node is ready for private fingerprint verification and registration."
