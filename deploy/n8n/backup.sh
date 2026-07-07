#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/.env}"
COMPOSE_FILE="${SCRIPT_DIR}/compose.yaml"
DRY_RUN=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --env-file) ENV_FILE="${2:?missing env file path}"; shift 2 ;;
    *) printf 'usage: %s [--dry-run] [--env-file PATH]\n' "$0" >&2; exit 64 ;;
  esac
done

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

set -a
[ -f "$ENV_FILE" ] || fail "missing env file: $ENV_FILE"
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

"${SCRIPT_DIR}/validate.sh" --dry-run --env-file "$ENV_FILE" >/dev/null
[ "${N8N_BACKUP_DIR:-}" != "${N8N_DATA_DIR:-}" ] || fail "backup directory must differ from data directory"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="${N8N_BACKUP_DIR}/n8n-sqlite-${timestamp}.tar.gz"

if [ "$DRY_RUN" -eq 1 ]; then
  printf 'dry-run: would stop compose service n8n, archive database.sqlite plus WAL/SHM state to %s, then restart n8n\n' "$archive"
  exit 0
fi

mkdir -p "$N8N_BACKUP_DIR"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" stop n8n
trap 'docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d n8n >/dev/null' EXIT

for file in database.sqlite database.sqlite-wal database.sqlite-shm; do
  [ -e "${N8N_DATA_DIR}/${file}" ] || [ "$file" != "database.sqlite" ] || fail "missing ${N8N_DATA_DIR}/${file}"
done

tar -C "$N8N_DATA_DIR" -czf "$archive" database.sqlite database.sqlite-wal database.sqlite-shm 2>/dev/null || \
  tar -C "$N8N_DATA_DIR" -czf "$archive" database.sqlite
printf 'created %s\n' "$archive"
