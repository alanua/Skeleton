# Home Edge Debian 13 External-Disk Migration Toolkit

This document defines the public-safe Home Edge migration contract for
`home-edge-01`. The toolkit is intentionally fail-closed: it preserves the
current Linux Mint internal SSD and prepares only an externally booted Debian 13
amd64 candidate for legacy-BIOS testing on the Fujitsu Q556/2.

No script in `ops/home_edge/debian13/` is approval by itself. Existing
filesystems, external repartitioning, reboot/test boot, and any later internal
cutover each require a separate explicit approval token bound to the current
machine, source identity, destination identity, timestamp, and operation.

## Protected Internal Disk

The following identifiers are regression fixtures for the existing internal SSD
and are never generic production inputs:

- `ata-SAMSUNG_MZ7PD128HCFV-000H1_S1MBNYAH205253`
- `wwn-0x5002538500000000`

The live root source, all parents, children, holders, mounted aliases, and any
disk matching the same serial or WWN are excluded from every external-target
operation. The toolkit never changes internal partition tables, filesystems,
bootloaders, boot order, EFI variables, NVRAM, or the Mint installation.

## External Identity

External storage must be selected by a stable `/dev/disk/by-id/...` identity and
must include model, serial or WWN, capacity, transport, and partition metadata.
`/dev/sdX`, `/dev/nvmeXnY`, `/dev/dm-*`, filesystem labels, and mount paths are
not accepted as authorization.

`inspect-external.sh` is read-only. It may call only `lsusb`, sysfs reads,
`udevadm info`, `lsblk`, `blkid`, `wipefs --no-act`, and SMART reads. It must
emit JSON and must not mount, repair, format, partition, install, or write.

## Rollback Evidence

The toolkit produces three immutable manifests:

- Inventory manifest: host, root source, protected disk aliases, external media
  identity, partition table, filesystem UUIDs, SMART summary, and timestamps.
- File backup manifest: source identity, destination identity, rsync mode,
  ACL/xattr/hardlink/numeric-ID preservation, exclude policy, file hashes, and
  timestamps.
- Full-device rollback-image manifest: source device identity, partition
  metadata, image path, byte count, SHA-256, SMART summary, and timestamps.

Rollback images are verified without restoration by read-only bounded inspection
through loop, guestfish, qemu-nbd-equivalent, or fixture-backed equivalents in
tests. Unverifiable or corrupted images are refused.

## Boot Contract

The required boot path is legacy BIOS. `bootstrap.sh --plan` installs GRUB
`i386-pc` only to the external disk. UEFI support may be added later, but it is
not required and no `efibootmgr`, EFI-variable, NVRAM, boot-order, or internal
GRUB command is permitted.

External Debian test boot uses the Fujitsu firmware one-time boot menu. If the
USB disk is removed or not selected, firmware returns to the untouched Linux Mint
internal SSD.

## First Boot Guard

`first-boot-guard.sh` enforces a bounded acceptance window. The Debian candidate
must create an explicit acceptance commit marker after software checks and
operator-required physical checks are complete. Without the marker the guard
records `rollback_required` and requests a reboot back to firmware selection.

## Acceptance

`acceptance.sh` emits machine-readable JSON with separate `sent`, `accepted`,
`applied`, and `physically_verified` states. Mandatory software gates must pass
for success. Physical-required checks cannot be marked verified by command exit
status alone and remain `physical_pending` until an operator attests them.

Samsung tablet-kiosk acceptance follows the current external kiosk contract:
network request evidence plus operator-confirmed visible state. It has no
removed local display workload and no legacy local display port requirement.

YouTube VA-API acceptance requires live hardware decode evidence and bounded
playback progress and dropped-frame metrics. Installed packages, flags, or
`vainfo` alone are insufficient.
