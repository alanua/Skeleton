[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Skeleton\ESPConnect\v1.1.18'),
    [ValidateRange(1024,65535)][int]$Port = 8765,
    [switch]$OpenBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Root = [IO.Path]::GetFullPath($InstallRoot)
$SkeletonRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Skeleton'))
if (-not $Root.StartsWith($SkeletonRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'install_root_outside_skeleton_localappdata'
}
if (-not (Test-Path -LiteralPath (Join-Path $Root 'index.html') -PathType Leaf)) {
    throw 'espconnect_not_installed'
}

$Prefix = "http://127.0.0.1:$Port/"
$Listener = [Net.HttpListener]::new()
$Listener.Prefixes.Add($Prefix)
$Listener.Start()

$Mime = @{
    '.html' = 'text/html; charset=utf-8'
    '.css' = 'text/css; charset=utf-8'
    '.js' = 'text/javascript; charset=utf-8'
    '.json' = 'application/json; charset=utf-8'
    '.svg' = 'image/svg+xml'
    '.png' = 'image/png'
    '.jpg' = 'image/jpeg'
    '.jpeg' = 'image/jpeg'
    '.webp' = 'image/webp'
    '.ico' = 'image/x-icon'
    '.wasm' = 'application/wasm'
    '.woff2' = 'font/woff2'
    '.txt' = 'text/plain; charset=utf-8'
}

function Resolve-EspConnectPath([string]$RequestPath) {
    $decoded = [Uri]::UnescapeDataString($RequestPath)
    if ($decoded.Contains([char]0)) { throw 'invalid_path' }
    $relative = $decoded.TrimStart('/').Replace('/', [IO.Path]::DirectorySeparatorChar)
    if ([string]::IsNullOrWhiteSpace($relative)) { $relative = 'index.html' }
    $candidate = [IO.Path]::GetFullPath((Join-Path $Root $relative))
    if ($candidate -ne $Root -and -not $candidate.StartsWith($Root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'path_escape'
    }
    if (Test-Path -LiteralPath $candidate -PathType Container) {
        $candidate = Join-Path $candidate 'index.html'
    }
    return $candidate
}

Write-Output ([ordered]@{
    status = 'serving'
    url = $Prefix
    bind = '127.0.0.1'
    localhost_only = $true
    stop = 'Ctrl+C'
} | ConvertTo-Json -Compress)

if ($OpenBrowser) {
    Start-Process $Prefix
}

try {
    while ($Listener.IsListening) {
        $Context = $Listener.GetContext()
        try {
            $Path = Resolve-EspConnectPath $Context.Request.Url.AbsolutePath
            if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
                $Context.Response.StatusCode = 404
                $Bytes = [Text.Encoding]::UTF8.GetBytes('not found')
            }
            else {
                $Context.Response.StatusCode = 200
                $Extension = [IO.Path]::GetExtension($Path).ToLowerInvariant()
                $Context.Response.ContentType = if ($Mime.ContainsKey($Extension)) { $Mime[$Extension] } else { 'application/octet-stream' }
                $Bytes = [IO.File]::ReadAllBytes($Path)
            }
            $Context.Response.Headers['Cache-Control'] = 'no-store'
            $Context.Response.ContentLength64 = $Bytes.Length
            $Context.Response.OutputStream.Write($Bytes, 0, $Bytes.Length)
        }
        catch {
            if ($Context.Response.OutputStream.CanWrite) {
                $Context.Response.StatusCode = 400
                $Bytes = [Text.Encoding]::UTF8.GetBytes('bad request')
                $Context.Response.ContentLength64 = $Bytes.Length
                $Context.Response.OutputStream.Write($Bytes, 0, $Bytes.Length)
            }
        }
        finally {
            $Context.Response.OutputStream.Close()
        }
    }
}
finally {
    $Listener.Stop()
    $Listener.Close()
}
