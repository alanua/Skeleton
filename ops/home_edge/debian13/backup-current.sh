#!/usr/bin/env bash
set -euo pipefail

PROTECTED_ATA="ata-SAMSUNG_MZ7PD128HCFV-000H1_S1MBNYAH205253"
PROTECTED_WWN="wwn-0x5002538500000000"
MODE="plan"
TARGET=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --plan|--dry-run) MODE="plan"; shift ;;
    --apply) MODE="apply"; shift ;;
    --target) TARGET="${2:-}"; shift 2 ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 64 ;;
  esac
done

reject() {
  printf '{"schema":"skeleton.home_edge.debian13.backup_current.v1","status":"rejected","reason":"%s","side_effects":[]}\n' "$1"
  exit 2
}

[[ -n "$TARGET" ]] || reject "identity_pending"
[[ "$TARGET" == /dev/disk/by-id/* ]] || reject "stable_by_id_required"
[[ "$TARGET" != *"$PROTECTED_ATA"* && "$TARGET" != *"$PROTECTED_WWN"* ]] || reject "protected_internal_disk"
[[ "$TARGET" != "${HE_FAKE_ROOT_SOURCE:-/dev/mapper/mint-root}" ]] || reject "live_root_target"
[[ "${HE_FAKE_TARGET_PARENT_OF_ROOT:-0}" != "1" ]] || reject "live_root_parent_or_child"
[[ "${HE_FAKE_IDENTITY_DRIFT:-0}" != "1" ]] || reject "identity_drift"
[[ "${HE_FAKE_TARGET_SYMLINK_UNSAFE:-0}" != "1" ]] || reject "symlink_target"
[[ "${HE_FAKE_INSUFFICIENT_SPACE:-0}" != "1" ]] || reject "insufficient_space"
[[ "${HE_FAKE_UNEXPECTED_MOUNT_OPTIONS:-0}" != "1" ]] || reject "unexpected_mount_options"
[[ "$TARGET" != "/" ]] || reject "root_target"

if [[ "$MODE" == "apply" && "${HE_APPROVE_BACKUP_WRITE:-}" != "backup-write:$TARGET" ]]; then
  reject "backup_write_approval_required"
fi

printf '{"schema":"skeleton.home_edge.debian13.backup_current.v1","status":"%s",' "$MODE"
printf '"source_identity":{"root":"%s","protected_ids":["%s","%s"]},' "${HE_FAKE_ROOT_SOURCE:-/dev/mapper/mint-root}" "$PROTECTED_ATA" "$PROTECTED_WWN"
printf '"target_identity":{"by_id":"%s","capacity_bytes":%s},' "$TARGET" "${HE_FAKE_EXTERNAL_CAPACITY_BYTES:-500000000000}"
printf '"preserves":["acls","xattrs","hardlinks","numeric_ids","private_state"],'
printf '"excludes":["private_operator_material"],'
printf '"commands":[["rsync","-aAXH","--numeric-ids","--one-file-system","--delete-delay","<source>","<staging>"]],'
printf '"manifest":{"sha256":"%s","timestamp":"%s"},"side_effects":[]}\n' "${HE_FAKE_MANIFEST_SHA256:-fixture-sha256}" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
