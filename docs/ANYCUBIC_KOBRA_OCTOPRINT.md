# Anycubic Kobra and OctoPrint source contract

This public-safe contract covers one device only: the original, first-generation Anycubic Kobra released in 2022. The Anycubic Vyper is not an active device and must never be selected by this integration.

## Target topology

The intended local path is:

`Cura -> G-code -> OctoPrint LXC -> Anycubic Kobra`

The printer remains on factory firmware. Klipper is explicitly out of scope. The reproducible OctoPrint target is a Debian 12 LXC on `brandenburg_proxmox`, with local-only networking and defaults of 1 vCPU, 1024 MiB RAM, and an 8 GiB root filesystem.

## Identity and USB boundary

The device identity is `identity_pending` until stable hardware evidence is observed. Acceptable evidence is either a complete USB VID/PID/serial tuple or a stable `/dev/serial/by-id/...` path, together with an exact match for the 2022 first-generation Kobra. Names resembling the target are not enough, and Vyper evidence is always rejected.

USB passthrough must use the stable by-id path rather than a changing `/dev/ttyUSB*` name. Actual passthrough configuration waits for the printer to be physically connected and identified. This source contract does not discover USB devices, create or start an LXC, or change Proxmox.

## Telemetry and notifications

OctoPrint is intended to expose read-only connection state, nozzle temperature, bed temperature, job/progress, and job history telemetry to Skeleton and Home Assistant. Skeleton/HA may use that telemetry for status and notifications. Telemetry declarations do not grant print, leveling, printer-control, or Home Assistant mutation authority.

## Hotbed NTC safety gate

Autonomous or unattended printing and bed leveling are forbidden until healthy bed-thermistor operation has been physically verified with fresh, complete evidence. The historical Hotbed NTC abnormal occurred during bed leveling, so labeling one static reading `physically_verified` must not clear the gate. Physical verification has three required components:

- a stable cold/ambient thermistor observation;
- a stable thermistor observation while the harness and bed are moved; and
- a controlled heating observation.

No numeric resistance, temperature, duration, or stability threshold is asserted because an exact measurement procedure for this hardware has not yet been verified. A command being `sent`, `accepted`, or `applied` is not physical verification. Missing, stale, abnormal, merely applied, or partially covered evidence fails closed, even when its state is `physically_verified` and its reported health is `healthy`. Only fresh, healthy evidence containing all three required physical observations clears the gate.

## Mutation and rollback boundary

This repository change is a declarative model, documentation, and synthetic tests only. It contains no credentials and performs no live OctoPrint, Proxmox, USB, printer, firmware, Home Assistant, print, or leveling action. There is consequently no live state to roll back. If a future separately approved deployment fails validation, its safe rollback is to remove or disable that deployment while leaving the printer on factory firmware and keeping the NTC safety gate closed.
