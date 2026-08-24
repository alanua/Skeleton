[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Skeleton\ESPConnect\v1.1.18'),
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Version = 'v1.1.18'
$ExpectedTagCommit = '77c79a01786881206ad9b3ccbe3db2ddb08f2989'
$Repository = 'thelastoutpostworkshop/ESPConnect'
$ReleaseApi = "https://api.github.com/repos/$Repository/releases/tags/$Version"
$TagApi = "https://api.github.com/repos/$Repository/git/ref/tags/$Version"
$AssetName = 'dist.zip'
$AssetUrl = "https://github.com/$Repository/releases/download/$Version/$AssetName"
$SkeletonRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Skeleton'))
$TargetRoot = [IO.Path]::GetFullPath($InstallRoot)

if (-not $TargetRoot.StartsWith($SkeletonRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'install_root_outside_skeleton_localappdata'
}

$plan = [ordered]@{
    status = if ($Apply) { 'apply_requested' } else { 'plan_only' }
    version = $Version
    expected_tag_commit = $ExpectedTagCommit
    asset = $AssetName
    install_scope = 'current_user_localappdata'
    bind_scope = 'localhost_only'
    destructive_operations_enabled = $false
}

if (-not $Apply) {
    $plan | ConvertTo-Json -Compress
    exit 0
}

$headers = @{ 'User-Agent' = 'Skeleton-ESP-Lab-Stage-B' }
$release = Invoke-RestMethod -Uri $ReleaseApi -Headers $headers -Method Get
if ($release.tag_name -ne $Version) {
    throw 'release_tag_mismatch'
}

$asset = @($release.assets | Where-Object { $_.name -eq $AssetName })
if ($asset.Count -ne 1) {
    throw 'release_asset_not_unique'
}
if (-not $asset[0].digest -or -not ([string]$asset[0].digest).StartsWith('sha256:', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'release_asset_sha256_digest_missing'
}
$ExpectedAssetSha256 = ([string]$asset[0].digest).Substring(7).ToLowerInvariant()
if ($ExpectedAssetSha256 -notmatch '^[0-9a-f]{64}$') {
    throw 'release_asset_sha256_digest_invalid'
}

$tagRef = Invoke-RestMethod -Uri $TagApi -Headers $headers -Method Get
$tagSha = [string]$tagRef.object.sha
if ([string]$tagRef.object.type -eq 'tag') {
    $tagObject = Invoke-RestMethod -Uri $tagRef.object.url -Headers $headers -Method Get
    $tagSha = [string]$tagObject.object.sha
}
if ($tagSha.ToLowerInvariant() -ne $ExpectedTagCommit) {
    throw 'release_tag_commit_mismatch'
}

$Parent = Split-Path -Parent $TargetRoot
New-Item -ItemType Directory -Force -Path $Parent | Out-Null
$TempRoot = Join-Path $env:TEMP ("skeleton-espconnect-" + [guid]::NewGuid().ToString('N'))
$ZipPath = Join-Path $TempRoot $AssetName
$ExtractRoot = Join-Path $TempRoot 'dist'
$StagingRoot = $TargetRoot + '.staging-' + [guid]::NewGuid().ToString('N')
$BackupRoot = $TargetRoot + '.previous'
$OldMoved = $false
$NewActivated = $false

try {
    New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null
    Invoke-WebRequest -Uri $AssetUrl -Headers $headers -OutFile $ZipPath -UseBasicParsing
    $ActualSha256 = (Get-FileHash -Algorithm SHA256 -Path $ZipPath).Hash.ToLowerInvariant()
    if ($ActualSha256 -ne $ExpectedAssetSha256) {
        throw 'release_asset_sha256_mismatch'
    }

    Expand-Archive -LiteralPath $ZipPath -DestinationPath $ExtractRoot -Force
    $Index = Get-ChildItem -LiteralPath $ExtractRoot -Filter 'index.html' -Recurse -File | Select-Object -First 1
    if (-not $Index) {
        throw 'espconnect_index_missing'
    }
    $DistRoot = $Index.Directory.FullName

    New-Item -ItemType Directory -Force -Path $StagingRoot | Out-Null
    Copy-Item -Path (Join-Path $DistRoot '*') -Destination $StagingRoot -Recurse -Force
    if (-not (Test-Path -LiteralPath (Join-Path $StagingRoot 'index.html') -PathType Leaf)) {
        throw 'staging_index_missing'
    }

    $Manifest = [ordered]@{
        schema = 'skeleton.esp_lab.espconnect_install.v1'
        version = $Version
        upstream_repository = $Repository
        tag_commit = $ExpectedTagCommit
        asset_name = $AssetName
        asset_sha256 = $ActualSha256
        installed_at_utc = [DateTime]::UtcNow.ToString('o')
        localhost_only = $true
        destructive_operations_enabled_by_installer = $false
    }
    $Manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $StagingRoot 'skeleton-install-manifest.json') -Encoding UTF8

    if (Test-Path -LiteralPath $BackupRoot) {
        Remove-Item -LiteralPath $BackupRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath $TargetRoot) {
        Move-Item -LiteralPath $TargetRoot -Destination $BackupRoot
        $OldMoved = $true
    }

    try {
        Move-Item -LiteralPath $StagingRoot -Destination $TargetRoot
        $NewActivated = $true
    }
    catch {
        if ($OldMoved -and -not (Test-Path -LiteralPath $TargetRoot) -and (Test-Path -LiteralPath $BackupRoot)) {
            Move-Item -LiteralPath $BackupRoot -Destination $TargetRoot
            $OldMoved = $false
        }
        throw
    }

    [ordered]@{
        status = 'installed'
        version = $Version
        tag_commit_verified = $true
        asset_sha256_verified = $true
        localhost_only = $true
        rollback_copy_retained = (Test-Path -LiteralPath $BackupRoot)
        next_action = 'run_scripts_espconnect_windows_stage_b_serve_ps1'
    } | ConvertTo-Json -Compress
}
finally {
    if (-not $NewActivated -and (Test-Path -LiteralPath $StagingRoot)) {
        Remove-Item -LiteralPath $StagingRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
