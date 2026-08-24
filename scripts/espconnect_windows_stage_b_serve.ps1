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

$Url = "http://127.0.0.1:$Port/"
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
    $decoded = [Uri]::UnescapeDataString($RequestPath.Split('?', 2)[0])
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

function Write-HttpResponse(
    [Net.Sockets.NetworkStream]$Stream,
    [int]$StatusCode,
    [string]$StatusText,
    [string]$ContentType,
    [byte[]]$Body,
    [bool]$HeadOnly
) {
    $header = "HTTP/1.1 $StatusCode $StatusText`r`nContent-Type: $ContentType`r`nContent-Length: $($Body.Length)`r`nCache-Control: no-store`r`nX-Content-Type-Options: nosniff`r`nConnection: close`r`n`r`n"
    $headerBytes = [Text.Encoding]::ASCII.GetBytes($header)
    $Stream.Write($headerBytes, 0, $headerBytes.Length)
    if (-not $HeadOnly -and $Body.Length -gt 0) {
        $Stream.Write($Body, 0, $Body.Length)
    }
    $Stream.Flush()
}

$Listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $Port)
$Listener.Start()
Write-Output ([ordered]@{
    status = 'serving'
    url = $Url
    bind = '127.0.0.1'
    localhost_only = $true
    http_sys = $false
    admin_required = $false
    stop = 'Ctrl+C'
} | ConvertTo-Json -Compress)

if ($OpenBrowser) {
    Start-Process $Url
}

try {
    while ($true) {
        $Client = $Listener.AcceptTcpClient()
        try {
            $Client.ReceiveTimeout = 5000
            $Client.SendTimeout = 5000
            $Stream = $Client.GetStream()
            $Reader = [IO.StreamReader]::new($Stream, [Text.Encoding]::ASCII, $false, 4096, $true)
            $RequestLine = $Reader.ReadLine()
            if ([string]::IsNullOrWhiteSpace($RequestLine)) {
                continue
            }
            $parts = $RequestLine.Split(' ')
            if ($parts.Count -ne 3 -or $parts[2] -notmatch '^HTTP/1\.[01]$') {
                $Body = [Text.Encoding]::UTF8.GetBytes('bad request')
                Write-HttpResponse $Stream 400 'Bad Request' 'text/plain; charset=utf-8' $Body $false
                continue
            }
            $Method = $parts[0].ToUpperInvariant()
            if ($Method -notin @('GET', 'HEAD')) {
                $Body = [Text.Encoding]::UTF8.GetBytes('method not allowed')
                Write-HttpResponse $Stream 405 'Method Not Allowed' 'text/plain; charset=utf-8' $Body $false
                continue
            }
            while ($true) {
                $line = $Reader.ReadLine()
                if ($null -eq $line -or $line.Length -eq 0) { break }
                if ($line.Length -gt 8192) { throw 'header_too_large' }
            }

            try {
                $Path = Resolve-EspConnectPath $parts[1]
                if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
                    $Body = [Text.Encoding]::UTF8.GetBytes('not found')
                    Write-HttpResponse $Stream 404 'Not Found' 'text/plain; charset=utf-8' $Body ($Method -eq 'HEAD')
                    continue
                }
                $Extension = [IO.Path]::GetExtension($Path).ToLowerInvariant()
                $ContentType = if ($Mime.ContainsKey($Extension)) { $Mime[$Extension] } else { 'application/octet-stream' }
                $Body = [IO.File]::ReadAllBytes($Path)
                Write-HttpResponse $Stream 200 'OK' $ContentType $Body ($Method -eq 'HEAD')
            }
            catch {
                $Body = [Text.Encoding]::UTF8.GetBytes('bad request')
                Write-HttpResponse $Stream 400 'Bad Request' 'text/plain; charset=utf-8' $Body ($Method -eq 'HEAD')
            }
        }
        finally {
            $Client.Close()
        }
    }
}
finally {
    $Listener.Stop()
}
