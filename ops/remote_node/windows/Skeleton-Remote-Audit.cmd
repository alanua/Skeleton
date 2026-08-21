@echo off
setlocal EnableExtensions

set "SCRIPT_URL=https://raw.githubusercontent.com/alanua/Skeleton/9330095a1849eb5fbe342fdb3caa5bb3b265efd0/ops/remote_node/windows/bootstrap.ps1"
set "WORK=%ProgramData%\Skeleton\RemoteAudit\bootstrap"
set "SCRIPT=%WORK%\bootstrap.ps1"
set "ENROLLMENT_URL=%~1"
if "%ENROLLMENT_URL%"=="" if not "%SKELETON_REMOTE_AUDIT_ENROLLMENT_URL%"=="" set "ENROLLMENT_URL=%SKELETON_REMOTE_AUDIT_ENROLLMENT_URL%"
if not "%ENROLLMENT_URL%"=="" set "SKELETON_REMOTE_AUDIT_ENROLLMENT_URL=%ENROLLMENT_URL%"

if not exist "%WORK%" mkdir "%WORK%" >nul 2>nul

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri '%SCRIPT_URL%' -OutFile '%SCRIPT%' } catch { Write-Error $_; exit 1 }"
if errorlevel 1 (
  echo Failed to download Skeleton Remote Audit bootstrap.
  pause
  exit /b 1
)

if "%ENROLLMENT_URL%"=="" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath powershell.exe -Verb RunAs -Wait -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File','%SCRIPT%')"
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$url = [Environment]::GetEnvironmentVariable('SKELETON_REMOTE_AUDIT_ENROLLMENT_URL','Process'); if (-not $url) { throw 'missing enrollment url' }; Start-Process -FilePath powershell.exe -Verb RunAs -Wait -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File','%SCRIPT%','-EnrollmentUrl',$url)"
)
if errorlevel 1 (
  echo Skeleton Remote Audit bootstrap did not complete.
  pause
  exit /b 1
)

echo Skeleton Remote Audit bootstrap finished.
pause
