#!/usr/bin/env bash
set -euo pipefail

SCRIPT_NAME=$(basename "$0")
PROTECTED_ATA="ata-SAMSUNG_MZ7PD128HCFV-000H1_S1MBNYAH205253"
PROTECTED_WWN="wwn-0x5002538500000000"

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

timestamp_utc() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

root_source() {
  if [[ -n "${HE_FAKE_ROOT_SOURCE:-}" ]]; then
    printf '%s' "$HE_FAKE_ROOT_SOURCE"
  else
    findmnt -n -o SOURCE /
  fi
}

if [[ "${1:-}" != "--json" && "${1:-}" != "" ]]; then
  printf '%s\n' "usage: $SCRIPT_NAME [--json]" >&2
  exit 64
fi

root=$(root_source)
printf '{'
printf '"schema":"skeleton.home_edge.debian13.inventory.v1",'
printf '"generated_at":"%s",' "$(timestamp_utc)"
printf '"node":"%s",' "$(json_escape "${HE_FAKE_HOSTNAME:-home-edge-01}")"
printf '"root_source":"%s",' "$(json_escape "$root")"
printf '"protected_internal_disk_ids":["%s","%s"],' "$PROTECTED_ATA" "$PROTECTED_WWN"
printf '"source_identity":{"model":"%s","serial":"%s","wwn":"%s"},' \
  "$(json_escape "${HE_FAKE_SOURCE_MODEL:-SAMSUNG MZ7PD128HCFV}")" \
  "$(json_escape "${HE_FAKE_SOURCE_SERIAL:-S1MBNYAH205253}")" \
  "$(json_escape "${HE_FAKE_SOURCE_WWN:-0x5002538500000000}")"
printf '"partition_metadata":%s,' "${HE_FAKE_PARTITION_METADATA:-[]}"
printf '"smart_summary":%s' "${HE_FAKE_SMART_SUMMARY:-{\"available\":false,\"reason\":\"not_collected_in_ci\"}}"
printf '}\n'
