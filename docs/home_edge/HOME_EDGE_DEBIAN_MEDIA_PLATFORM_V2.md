# Home Edge Debian Media Platform v2

Status: **target clean-install architecture**  
Target: `home-edge-01`, Debian 13 stable amd64  
Decision: 2026-07-27

## System decision

Home Edge remains Skeleton's controlled physical executor. The media node will be rebuilt on a minimal Debian 13 installation instead of carrying forward Linux Mint, GNOME and Waydroid.

Disk erase is a separate destructive gate. It is forbidden until an offline backup, a boot-tested full-disk rollback image, installation media and a verified restore manifest exist.

Waydroid is not installed on the new system. Its last working state remains only in the rollback image. VLC is not installed. MPV is the only general native media player.

## Base system

- Hostname `home-edge-01`
- User `valertos08`, UID 1000
- GPT/UEFI, Ethernet and OpenSSH
- One persistent Sway Wayland session through greetd
- PipeWire and WirePlumber
- Chromium TV profile for YouTube and web applications
- MPV IPC plus VAAPI for HLS, DASH, IPTV, files and direct streams
- Native systemd services; no parallel GNOME or Android desktop

## Protected Home Edge slice

The rebuild must restore and independently verify:

- `home_edge_exec`, approvals and audit receipts
- MemoryGate, device registry and state database
- Brother MFP conveyor and document archive
- local inference worker and SSH bridge
- tunnel and remote access
- photo-frame service
- HyperHDR and WLED control

## Media services

- `skeleton-media-orchestrator`: only media-mode writer
- `skeleton-media-input-broker`: semantic phone, gyro, touchpad and HID input
- `skeleton-media-browser`: dedicated Chromium TV session
- `skeleton-media-player`: MPV direct playback
- `skeleton-iptv-adapter`: private M3U/XMLTV, EPG and source health
- `skeleton-game-session`: Steam/RetroArch; Gamescope only around a game
- `skeleton-media-audio`: unified sink and volume policy
- `skeleton-hyperhdr-session`: capture and physical output verification
- Home mobile app: remote, touchpad, native keyboard, gyro and gamepad

Only one heavy foreground renderer may run at a time. This machine is not a real-time transcoder. Prefer H.264, HEVC and VP9; avoid AV1 on Intel HD 610.

## Game-mode invariant

Whenever the orchestrator reports `games`, Home `/remote` automatically becomes the landscape universal gamepad with D-pad, A/B/X/Y, L/R, Start and Select. The ordinary media remote and touchpad are hidden. A physical USB/Bluetooth gamepad may operate in parallel.

## Migration gates

1. `G0`: architecture branch and migration tooling.
2. `G1`: verified offline backup on another disk.
3. `G2`: boot-tested full image of the current Mint disk.
4. `G3`: operator boots Debian installer and confirms the target disk; create `valertos08`, Ethernet and SSH.
5. `G4`: Skeleton runs the remote bootstrap.
6. `G5`: Skeleton restores private state and vendor components.
7. `G6`: physical and software acceptance of executor, MFP, tunnel, Home UI, MPV/VAAPI, Chromium, HyperHDR, photo frame, inference and gamepad.

GitHub must never contain HMAC secrets, SSH keys, Tailscale identity, private playlists, XMLTV URLs, cookies, browser profiles or personal documents.
