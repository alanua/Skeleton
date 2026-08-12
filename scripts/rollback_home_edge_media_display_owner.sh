#!/usr/bin/env bash
set -euo pipefail

TARGET_LIB="$HOME/.local/lib/skeleton/home_edge/media_display_ownership.py"
TARGET_BIN="$HOME/.local/bin/home-edge-media-display-owner"
STATE="$HOME/.local/state/skeleton/home-edge-media-display-owner"
BACKUP="$STATE/rollback"

rm -f "$TARGET_LIB" "$TARGET_BIN"
if [[ -f "$BACKUP/media_display_ownership.py.previous" ]]; then
  mv "$BACKUP/media_display_ownership.py.previous" "$TARGET_LIB"
fi
if [[ -f "$BACKUP/home-edge-media-display-owner.previous" ]]; then
  mv "$BACKUP/home-edge-media-display-owner.previous" "$TARGET_BIN"
fi
rm -f "$BACKUP/captured"

printf 'rollback_status=APPLIED\n'
printf 'operation=home_edge_media_display_owner_rollback\n'
printf 'mutating_media_actions=false\n'
