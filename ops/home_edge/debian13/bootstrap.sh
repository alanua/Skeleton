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
  printf '{"schema":"skeleton.home_edge.debian13.bootstrap.v1","status":"rejected","reason":"%s","side_effects":[]}\n' "$1"
  exit 2
}

[[ "${HE_FAKE_OS_ID:-debian}" == "debian" && "${HE_FAKE_OS_VERSION_ID:-13}" == "13" && "${HE_FAKE_ARCH:-amd64}" == "amd64" ]] || reject "debian_13_amd64_required"
[[ "${HE_FAKE_HOSTNAME:-home-edge-01}" == "home-edge-01" ]] || reject "hostname_home_edge_01_required"
[[ "${HE_FAKE_UID1000_PRESENT:-1}" == "1" ]] || reject "uid_1000_required"
[[ "${HE_FAKE_HARDWARE:-Fujitsu Q556/2}" == "Fujitsu Q556/2" ]] || reject "hardware_required"
[[ "${HE_FAKE_BOOT_MODE:-legacy-bios}" == "legacy-bios" ]] || reject "legacy_bios_required"
[[ -n "$TARGET" ]] || reject "identity_pending"
[[ "$TARGET" == /dev/disk/by-id/* ]] || reject "stable_by_id_required"
[[ "$TARGET" != *"$PROTECTED_ATA"* && "$TARGET" != *"$PROTECTED_WWN"* ]] || reject "protected_internal_disk"
[[ "${HE_FAKE_PROTECTED_PRESENT:-1}" == "1" ]] || reject "protected_internal_missing"

if [[ "$MODE" == "apply" && "${HE_APPROVE_EXTERNAL_REPARTITION:-}" != "external-repartition:$TARGET" ]]; then
  reject "external_repartition_approval_required"
fi

printf '{"schema":"skeleton.home_edge.debian13.bootstrap.v1","status":"%s",' "$MODE"
printf '"node":"home-edge-01","target":"%s",' "$TARGET"
printf '"boot":{"mode":"legacy-bios","grub_target":"i386-pc","install_device":"external_only"},'
printf '"commands":[["parted","%s","mklabel","gpt"],["mkfs.ext4","%s-part2"],["debootstrap","trixie","<target-root>"],["grub-install","--target=i386-pc","--boot-directory=<target-root>/boot","%s"]],' "$TARGET" "$TARGET" "$TARGET"
printf '"forbidden_absent":["efibootmgr","NVRAM","boot_order_change","internal_grub"],"side_effects":[]}\n'
