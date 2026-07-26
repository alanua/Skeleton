# Future media-node hardware direction

Status: **research baseline; no purchase approved**  
Updated: 2026-07-27

## Architecture decision

The next hardware generation should separate responsibilities instead of forcing an Android box to become the Home Edge core.

- The Debian x86 node remains the trusted Home Edge control plane: executor, MemoryGate, MFP, tunnel, document services and automation.
- A new display/media node may be added later for 4K playback and games.
- A certified Android/CoreELEC box is acceptable only as a secondary TV endpoint for DRM applications or appliance-style Kodi playback.

This separation lets the current Fujitsu continue as a headless fallback or control node after a stronger media endpoint is purchased.

## Preferred future main node

Target class: AMD Ryzen 7 8845HS / Ryzen 9 8945HS or a newer equivalent with Radeon 780M-class graphics.

Minimum configuration:

- 8 cores / 16 threads
- 32 GiB replaceable DDR5
- 1 TB NVMe; two M.2 slots preferred
- HDMI 2.1 plus DisplayPort/USB4
- at least one 2.5 GbE port; two preferred
- replaceable Wi-Fi/Bluetooth module
- UEFI settings for power-on after outage and Wake-on-LAN
- Linux-supported GPU, audio and network controllers

Why: this class combines strong hardware video decode, enough CPU for Home Edge services and substantially better integrated-graphics gaming than the current Pentium/HD 610 system. It can remain a single-box system if desired.

Reference design, not a purchase commitment: Minisforum UM890 Pro class — Ryzen 9 8945HS, Radeon 780M, two DDR5 SODIMM slots, two PCIe 4.0 M.2 slots, HDMI 2.1, two USB4 ports and dual 2.5 GbE.

## Efficient media-only x86 endpoint

Target class: Intel N150/N250 mini PC with 16 GiB RAM and 512 GB NVMe.

Use only for:

- Chromium/MPV/Kodi playback
- IPTV
- HyperHDR capture
- lightweight emulation

Do not select this class as the long-term all-in-one node when modern PC gaming, local inference or heavy browser workloads are expected. Confirm Linux VAAPI, HDMI output mode, Ethernet controller and cooling on the exact model before purchase.

## Strong Intel alternative

Target class: Intel Core Ultra 5 H-series or newer with integrated Arc graphics, 32 GiB RAM and 1 TB NVMe.

This is attractive when Intel media support and AV1 encode/decode matter more than maximum integrated-GPU game performance. The exact mini-PC implementation must still be checked for HDMI 2.1, Linux audio, suspend/wake behavior and replaceable storage.

## Android/CoreELEC endpoints

### Ugoos AM8 Pro

Useful as a flashable dedicated playback endpoint:

- Amlogic S928X-J
- 8 GiB RAM / 64 GB eMMC
- HDMI 2.1, Gigabit Ethernet
- AV1/VP9/HEVC hardware decode
- vendor reflashing instructions
- supported by CoreELEC device tooling

It is not suitable as the primary Home Edge node because the platform depends on vendor kernels and cannot replace the x86 MFP, browser, local-service and game stack.

### Homatics Box R 4K Plus

Useful when certified commercial streaming applications matter:

- 4 GiB RAM / 32 GB eMMC
- Gigabit Ethernet and Wi-Fi 6
- Widevine L1 and PlayReady SL3000
- HDMI 2.1, Dolby Vision and Dolby Atmos
- CoreELEC support exists

It is a secondary certified TV client, not a replacement for the Debian Home Edge core.

## Procurement rejection rules

Reject any candidate with:

- 100 Mbit Ethernet or Wi-Fi-only networking
- eMMC-only storage for the primary node
- soldered 8 GiB RAM for the primary node
- no documented Linux graphics/audio support
- no BIOS recovery or power-loss restart controls
- unknown/no-name Android firmware with no recovery image
- one storage slot when games or recording are planned
- thermal throttling under sustained video plus HyperHDR load

## Decision timing

Do not buy hardware before the Debian rebuild is stable. After at least 30 days of measured operation, collect CPU, GPU, RAM, storage, playback and game-load data. Then compare exact Germany-market models, prices, noise and Linux compatibility against these requirements.
