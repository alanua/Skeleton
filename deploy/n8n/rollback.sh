#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/.env}"
DRY_RUN=0
ARCHIVE=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --env-file) ENV_FILE="${2:?missing env file path}"; shift 2 ;;
    --archive) ARCHIVE="${2:?missing archive path}"; shift 2 ;;
    *) printf 'usage: %s --archive PATH [--dry-run] [--env-file PATH]\n' "$0" >&2; exit 64 ;;
  esac
done

[ -n "$ARCHIVE" ] || { printf 'error: missing --archive\n' >&2; exit 1; }

if [ "$DRY_RUN" -eq 1 ]; then
  "${SCRIPT_DIR}/restore.sh" --dry-run --env-file "$ENV_FILE" --archive "$ARCHIVE"
  exit 0
fi

"${SCRIPT_DIR}/restore.sh" --env-file "$ENV_FILE" --archive "$ARCHIVE"
