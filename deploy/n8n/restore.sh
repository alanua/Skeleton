#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/.env}"
COMPOSE_FILE="${SCRIPT_DIR}/compose.yaml"
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

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
[ -n "$ARCHIVE" ] || fail "missing --archive"
case "$ARCHIVE" in
  /*) ;;
  *) fail "archive path must be absolute" ;;
esac

set -a
[ -f "$ENV_FILE" ] || fail "missing env file: $ENV_FILE"
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

"${SCRIPT_DIR}/validate.sh" --dry-run --env-file "$ENV_FILE" >/dev/null

case "$ARCHIVE" in
  "$N8N_DATA_DIR"|"$N8N_DATA_DIR"/*) fail "archive must not be inside N8N_DATA_DIR" ;;
esac

if [ "$DRY_RUN" -eq 1 ]; then
  printf 'dry-run: would validate %s in isolation, stop n8n, replace database.sqlite/WAL/SHM, and restart n8n\n' "$ARCHIVE"
  exit 0
fi

[ -f "$ARCHIVE" ] || fail "archive does not exist: $ARCHIVE"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
tar -tzf "$ARCHIVE" | grep -Eq '(^|/)database\.sqlite$' || fail "archive lacks database.sqlite"
tar -xzf "$ARCHIVE" -C "$tmpdir"
[ -f "$tmpdir/database.sqlite" ] || fail "archive must place database.sqlite at archive root"

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" stop n8n
trap 'docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d n8n >/dev/null; rm -rf "$tmpdir"' EXIT
install -m 600 "$tmpdir/database.sqlite" "$N8N_DATA_DIR/database.sqlite"
for file in database.sqlite-wal database.sqlite-shm; do
  if [ -f "$tmpdir/$file" ]; then
    install -m 600 "$tmpdir/$file" "$N8N_DATA_DIR/$file"
  else
    rm -f "$N8N_DATA_DIR/$file"
  fi
done
printf 'restored %s\n' "$ARCHIVE"
