#!/usr/bin/env bash
set -euo pipefail

WINDOW_SECONDS="${HE_ACCEPTANCE_WINDOW_SECONDS:-900}"
MARKER="${HE_ACCEPTANCE_COMMIT_MARKER:-/var/lib/skeleton/home-edge-acceptance.commit}"
MODE="${1:---check}"
TARGET=""

if [[ "$MODE" == "--arm" ]]; then
  shift
  if [[ "${1:-}" == "--target" ]]; then
    TARGET="${2:-}"
  fi
  if [[ -z "$TARGET" || "$TARGET" != /dev/disk/by-id/* ]]; then
    printf '{"schema":"skeleton.home_edge.debian13.first_boot_guard.v1","status":"rejected","reason":"stable_by_id_required"}\n'
    exit 2
  fi
  if [[ "${HE_APPROVE_REBOOT_TEST_BOOT:-}" != "reboot-test-boot:$TARGET" ]]; then
    printf '{"schema":"skeleton.home_edge.debian13.first_boot_guard.v1","status":"rejected","reason":"reboot_test_boot_approval_required"}\n'
    exit 2
  fi
  printf '{"schema":"skeleton.home_edge.debian13.first_boot_guard.v1","status":"armed","target":"%s","window_seconds":%s,"firmware":"one_time_usb_boot"}\n' "$TARGET" "$WINDOW_SECONDS"
  exit 0
fi

if [[ -f "$MARKER" ]]; then
  printf '{"schema":"skeleton.home_edge.debian13.first_boot_guard.v1","status":"accepted","marker":"%s","action":"stay_booted"}\n' "$MARKER"
else
  printf '{"schema":"skeleton.home_edge.debian13.first_boot_guard.v1","status":"rollback_required","window_seconds":%s,"action":"request_reboot_to_firmware"}\n' "$WINDOW_SECONDS"
  exit 3
fi
