@echo off
setlocal EnableExtensions

set "SCRIPT_URL=https://raw.githubusercontent.com/alanua/Skeleton/main/ops/remote_node/windows/bootstrap.ps1"
set "WORK=%ProgramData%\Skeleton\RemoteAudit\bootstrap"
set "SCRIPT=%WORK%\bootstrap.ps1"

if not exist "%WORK%" mkdir "%WORK%" >nul 2>nul

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri '%SCRIPT_URL%' -OutFile '%SCRIPT%' } catch { Write-Error $_; exit 1 }"
if errorlevel 1 (
  echo Failed to download Skeleton Remote Audit bootstrap.
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath powershell.exe -Verb RunAs -Wait -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File','%SCRIPT%')"
if errorlevel 1 (
  echo Skeleton Remote Audit bootstrap did not complete.
  pause
  exit /b 1
)

echo Skeleton Remote Audit bootstrap finished.
pause
