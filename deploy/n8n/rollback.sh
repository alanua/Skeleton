#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/.env}"
DRY_RUN=0
ARCHIVE=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --env-file) ENV_FILE="${2:?missing env file path}"; shift 2 ;;
    --archive) ARCHIVE="${2:?missing rollback archive path}"; shift 2 ;;
    *) printf 'usage: %s --archive PATH [--dry-run] [--env-file PATH]\n' "$0" >&2; exit 64 ;;
  esac
done

[ -n "$ARCHIVE" ] || { printf 'error: missing --archive\n' >&2; exit 1; }
case "$ARCHIVE" in
  /*) ;;
  *) printf 'error: rollback archive path must be absolute\n' >&2; exit 1 ;;
esac

if [ "$DRY_RUN" -eq 1 ]; then
  "${SCRIPT_DIR}/restore.sh" --dry-run --env-file "$ENV_FILE" --archive "$ARCHIVE"
  printf 'dry-run: rollback archive validation would be enforced before any replacement\n'
  exit 0
fi

"${SCRIPT_DIR}/restore.sh" --env-file "$ENV_FILE" --archive "$ARCHIVE"
printf 'rollback restored %s\n' "$ARCHIVE"
