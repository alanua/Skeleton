#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/compose.yaml"
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/.env}"
DRY_RUN=0

usage() {
  printf 'usage: %s [--dry-run] [--env-file PATH]\n' "$0"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --env-file)
      [ "$#" -ge 2 ] || { usage >&2; exit 64; }
      ENV_FILE="$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 64 ;;
  esac
done

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

load_env() {
  [ -f "$ENV_FILE" ] || fail "missing env file: $ENV_FILE"
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
}

reject_descriptor() {
  local name="$1"
  local value="${!name:-}"
  [ -n "$value" ] || fail "$name is required"
  case "$value" in
    *REPLACE*|*PLACEHOLDER*|*DESCRIPTOR*|*example*|*changeme*|*CHANGE_ME*)
      fail "$name still contains a descriptor"
      ;;
  esac
}

reject_unsafe_path() {
  local name="$1"
  local value="${!name:-}"
  [ -n "$value" ] || fail "$name is required"
  case "$value" in
    /*) ;;
    *) fail "$name must be an absolute path" ;;
  esac
  case "$value" in
    /|/tmp|/var|/srv|/home|/home/*/.ssh|*Skeleton*|*skeleton*private*|*private*memory*|*private[_-]memory*)
      fail "$name is too broad or references forbidden private-memory scope: $value"
      ;;
  esac
}

assert_owner_only_dir() {
  local path="$1"
  if [ "$DRY_RUN" -eq 1 ]; then
    return 0
  fi
  [ -d "$path" ] || fail "directory does not exist: $path"
  local mode
  mode="$(stat -c '%a' "$path")"
  [ "$mode" = "700" ] || fail "$path must be owner-only mode 700, found $mode"
}

load_env
reject_descriptor N8N_ENCRYPTION_KEY
reject_unsafe_path N8N_DATA_DIR
reject_unsafe_path N8N_BACKUP_DIR
[ "$N8N_DATA_DIR" != "$N8N_BACKUP_DIR" ] || fail "N8N_DATA_DIR and N8N_BACKUP_DIR must differ"
case "$N8N_BACKUP_DIR" in
  "$N8N_DATA_DIR"|"$N8N_DATA_DIR"/*) fail "N8N_BACKUP_DIR must not be inside N8N_DATA_DIR" ;;
esac
assert_owner_only_dir "$N8N_DATA_DIR"

grep -q 'n8nio/n8n:1\.97\.1@sha256:' "$COMPOSE_FILE" || fail "compose image is not version and digest pinned"
grep -q 'DB_SQLITE_DATABASE: /home/node/.n8n/database.sqlite' "$COMPOSE_FILE" || fail "compose must use n8n database.sqlite"
grep -q 'DB_SQLITE_POOL_SIZE: "2"' "$COMPOSE_FILE" || fail "DB_SQLITE_POOL_SIZE must be explicit and nonzero"
grep -q '127.0.0.1:${N8N_EDITOR_PORT:-5678}:5678' "$COMPOSE_FILE" || fail "editor bind must default to loopback"

if command -v docker >/dev/null 2>&1 && [ "$DRY_RUN" -eq 0 ]; then
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config >/dev/null
fi

printf 'n8n deployment package validation passed%s\n' "$([ "$DRY_RUN" -eq 1 ] && printf ' (dry-run)')"
