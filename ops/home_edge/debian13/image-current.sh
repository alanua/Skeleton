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
  printf '{"schema":"skeleton.home_edge.debian13.image_current.v1","status":"rejected","reason":"%s","side_effects":[]}\n' "$1"
  exit 2
}

[[ -n "$TARGET" ]] || reject "identity_pending"
[[ "$TARGET" == /dev/disk/by-id/* ]] || reject "stable_by_id_required"
[[ "$TARGET" != *"$PROTECTED_ATA"* && "$TARGET" != *"$PROTECTED_WWN"* ]] || reject "protected_internal_disk"
[[ "${HE_FAKE_INSUFFICIENT_SPACE:-0}" != "1" ]] || reject "insufficient_space"

if [[ "$MODE" == "apply" && "${HE_APPROVE_IMAGE_WRITE:-}" != "image-write:$TARGET" ]]; then
  reject "image_write_approval_required"
fi

printf '{"schema":"skeleton.home_edge.debian13.rollback_image.v1","status":"%s",' "$MODE"
printf '"source_identity":{"device":"%s","protected_ids":["%s","%s"]},' "${HE_FAKE_ROOT_PARENT:-/dev/disk/by-id/ata-SAMSUNG_MZ7PD128HCFV-000H1_S1MBNYAH205253}" "$PROTECTED_ATA" "$PROTECTED_WWN"
printf '"target_identity":{"by_id":"%s"},' "$TARGET"
printf '"partition_metadata":%s,' "${HE_FAKE_PARTITION_METADATA:-[]}"
printf '"smart_summary":%s,' "${HE_FAKE_SMART_SUMMARY:-{\"available\":false,\"reason\":\"not_collected_in_ci\"}}"
printf '"image":{"path":"<external>/rollback/home-edge-01.img","bytes":%s,"sha256":"%s"},' "${HE_FAKE_IMAGE_BYTES:-128000000000}" "${HE_FAKE_IMAGE_SHA256:-fixture-image-sha256}"
printf '"verification":"readonly_bounded_required","side_effects":[]}\n'
