#!/usr/bin/env bash
set -euo pipefail

MODE="dry-run"
TARGET_ROOT=""
MANIFEST=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run|--plan) MODE="dry-run"; shift ;;
    --stage) MODE="stage"; shift ;;
    --target-root) TARGET_ROOT="${2:-}"; shift 2 ;;
    --manifest) MANIFEST="${2:-}"; shift 2 ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 64 ;;
  esac
done

reject() {
  printf '{"schema":"skeleton.home_edge.debian13.restore_private.v1","status":"rejected","reason":"%s","side_effects":[]}\n' "$1"
  exit 2
}

[[ -n "$TARGET_ROOT" ]] || reject "target_root_required"
[[ "$TARGET_ROOT" != "/" ]] || reject "live_root_restore_forbidden"
[[ "$TARGET_ROOT" == /mnt/home-edge-stage/* || "$TARGET_ROOT" == "${HE_FAKE_ALLOWED_STAGE_ROOT:-/tmp/home-edge-stage}"* ]] || reject "staged_target_required"
[[ -n "$MANIFEST" ]] || reject "manifest_required"
[[ "$MODE" != "stage" || "${HE_APPROVE_RESTORE_STAGE:-}" == "restore-stage:$TARGET_ROOT" ]] || reject "restore_stage_approval_required"

printf '{"schema":"skeleton.home_edge.debian13.restore_private.v1","status":"%s","target_root":"%s","manifest":"%s","verifies":["hashes","permissions","numeric_ids","acls","xattrs"],"live_root_rsync":false,"side_effects":[]}\n' "$MODE" "$TARGET_ROOT" "$MANIFEST"
