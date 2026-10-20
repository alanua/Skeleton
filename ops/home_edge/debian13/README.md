# Debian 13 External-Disk Migration Toolkit

This directory contains guarded planning and evidence scripts for the Home Edge
Debian 13 migration. The scripts are safe to syntax-check and test with fixtures
in CI. They are not a live migration approval and must not be used here to run a
backup, mount, partition, install, reboot, device probe, or service mutation.

## Entry Points

- `inventory.sh`: immutable host, root, protected-disk, external-identity JSON.
- `inspect-external.sh`: read-only external media inspection JSON.
- `backup-current.sh`: gated file-level backup plan/apply receipt.
- `image-current.sh`: gated full-device rollback image plan/apply receipt.
- `verify-backup.sh`: read-only manifest and image verification.
- `bootstrap.sh`: gated external-only Debian 13 bootstrap plan/apply receipt.
- `restore-private.sh`: dry-run and staged restore verification only.
- `first-boot-guard.sh`: bounded acceptance marker guard.
- `acceptance.sh`: machine-readable acceptance matrix.

## Approval Gates

The gates are independent and non-transferable:

- `HE_APPROVE_BACKUP_WRITE=backup-write:<external-by-id>`
- `HE_APPROVE_IMAGE_WRITE=image-write:<external-by-id>`
- `HE_APPROVE_EXTERNAL_REPARTITION=external-repartition:<external-by-id>`
- `HE_APPROVE_REBOOT_TEST_BOOT=reboot-test-boot:<external-by-id>`
- `HE_APPROVE_INTERNAL_CUTOVER=internal-cutover:<protected-by-id>`

The internal cutover gate is documented for future review only. No script in
this toolkit performs internal cutover.

## Non-Live Test Fixtures

Tests may provide fixture values through `HE_FAKE_*` environment variables. Those
paths are CI-only and do not authorize live operations.
