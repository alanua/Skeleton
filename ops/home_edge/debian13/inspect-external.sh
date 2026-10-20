#!/usr/bin/env bash
set -euo pipefail

PROTECTED_ATA="ata-SAMSUNG_MZ7PD128HCFV-000H1_S1MBNYAH205253"
PROTECTED_WWN="wwn-0x5002538500000000"

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

is_stable_by_id() {
  [[ "$1" == /dev/disk/by-id/* ]]
}

if [[ "${1:-}" != "--json" && "${1:-}" != "" ]]; then
  printf '%s\n' "usage: inspect-external.sh [--json]" >&2
  exit 64
fi

if [[ -z "${HE_FAKE_EXTERNAL_ID:-}" ]]; then
  printf '{"schema":"skeleton.home_edge.debian13.inspect_external.v1","status":"identity_pending","external_candidates":[],"side_effects":[]}\n'
  exit 0
fi

if ! is_stable_by_id "$HE_FAKE_EXTERNAL_ID"; then
  printf '{"schema":"skeleton.home_edge.debian13.inspect_external.v1","status":"rejected","reason":"stable_by_id_required","side_effects":[]}\n'
  exit 2
fi

if [[ "$HE_FAKE_EXTERNAL_ID" == *"$PROTECTED_ATA"* || "$HE_FAKE_EXTERNAL_ID" == *"$PROTECTED_WWN"* ]]; then
  printf '{"schema":"skeleton.home_edge.debian13.inspect_external.v1","status":"rejected","reason":"protected_internal_disk","side_effects":[]}\n'
  exit 2
fi

printf '{"schema":"skeleton.home_edge.debian13.inspect_external.v1","status":"candidate","external_candidates":[{'
printf '"by_id":"%s",' "$(json_escape "$HE_FAKE_EXTERNAL_ID")"
printf '"model":"%s",' "$(json_escape "${HE_FAKE_EXTERNAL_MODEL:-External USB SSD}")"
printf '"serial":"%s",' "$(json_escape "${HE_FAKE_EXTERNAL_SERIAL:-EXTSAFE0001}")"
printf '"wwn":"%s",' "$(json_escape "${HE_FAKE_EXTERNAL_WWN:-}")"
printf '"capacity_bytes":%s,' "${HE_FAKE_EXTERNAL_CAPACITY_BYTES:-500000000000}"
printf '"transport":"%s",' "$(json_escape "${HE_FAKE_EXTERNAL_TRANSPORT:-usb}")"
printf '"partition_metadata":%s,' "${HE_FAKE_EXTERNAL_PARTITIONS:-[]}"
printf '"smart_summary":%s' "${HE_FAKE_EXTERNAL_SMART:-{\"available\":false,\"reason\":\"not_collected_in_ci\"}}"
printf '}],"allowed_read_commands":["lsusb","sysfs","udevadm info","lsblk","blkid","wipefs --no-act","smartctl read"],"side_effects":[]}\n'
