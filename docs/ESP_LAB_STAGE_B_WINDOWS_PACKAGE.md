# ESP Lab Stage B — Windows ESPConnect package

This runbook is an operational companion to `docs/HOME_EDGE_ESP_LAB.md`. It does not create a second ESP Lab authority or expand the Stage A read-only automation boundary.

## Pinned upstream

- Repository: `thelastoutpostworkshop/ESPConnect`
- License: MIT
- Stable release: `v1.1.18`
- Exact release/tag commit: `77c79a01786881206ad9b3ccbe3db2ddb08f2989`
- Package: official release asset `dist.zip`

The installer queries the GitHub release metadata at apply time, requires the `dist.zip` asset to expose a SHA-256 digest, verifies the downloaded archive against that digest, resolves the release tag and requires the exact pinned commit above. The verified digest and commit are recorded in the local install manifest.

## Architecture boundary

ESPConnect is the local manual operator UI on DE-PC. Skeleton remains the typed control/audit/authorization layer. The automated connector and ESPConnect must never compete for the same serial port at the same time.

Stage B does not add a second scheduler, task queue, state authority, generic remote shell or firmware automation path.

## Install preparation

The installer is plan-only unless `-Apply` is explicit. The default install path is current-user local storage:

`%LOCALAPPDATA%\Skeleton\ESPConnect\v1.1.18`

Plan:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\espconnect_windows_stage_b_install.ps1
```

Apply only after the plan is accepted on DE-PC:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\espconnect_windows_stage_b_install.ps1 -Apply
```

Installation is staged before activation. An existing target is moved to `.previous`; if activation of the new staging tree fails, the previous target is restored. A successful replacement retains `.previous` as a rollback copy.

## Local-only serving

Serve the static app on loopback only:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\espconnect_windows_stage_b_serve.ps1 -OpenBrowser
```

Default URL: `http://127.0.0.1:8765/`.

The helper does not bind `0.0.0.0`, create a Windows service, add a scheduled task or open a firewall port. Stop it with Ctrl+C.

## First real-board pilot — read-only

1. Close Arduino IDE, PlatformIO serial monitor, the Skeleton automated connector and any other program using the board COM port.
2. Connect one known ESP board with a data-capable USB cable.
3. Open the localhost ESPConnect page in Chromium/Edge/Chrome.
4. Connect to the selected serial device.
5. Inspect Device Info and confirm chip family/flash information only.
6. Do not use Flash, Erase, filesystem write/format, register write, restore or other destructive functions during this pilot.
7. Disconnect in ESPConnect before starting the Skeleton automated connector.
8. Run the separate Skeleton Stage A read-only identify/flash-id job and compare only safe aggregate expectations; private device evidence remains local/private.

## Safety gates

ESPConnect itself exposes upstream manual write capabilities. Stage B installation does not authorize their use through Skeleton. Skeleton Stage C remains blocked until all of these exist:

- durable idempotency across process restart;
- explicit ActionGate-backed destructive-operation contracts;
- exact device/family/flash-size preflight;
- backup-before-write where supported;
- exact firmware artifact hash and provenance;
- post-write verification and recovery/rollback receipt.

The first approved production flash canary remains Lavalamp, but no flash is part of Stage B.
