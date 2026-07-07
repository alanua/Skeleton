#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/compose.yaml"
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/.env}"
DRY_RUN=0
IMAGE_PIN="n8nio/n8n:2.29.7@sha256:e0264b531fb97c68ece58a650173bd981f1663947281013f4a46749c15a8abc5"

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

is_allowed_key() {
  case "$1" in
    COMPOSE_PROJECT_NAME|N8N_COMPOSE_PROJECT_NAME|N8N_EDITOR_PORT|N8N_HOST|N8N_DATA_DIR|N8N_BACKUP_DIR|N8N_ENCRYPTION_KEY) return 0 ;;
    *) return 1 ;;
  esac
}

assert_owner_only_file() {
  local path="$1" mode
  [ -f "$path" ] || fail "missing env file: $path"
  mode="$(stat -c '%a' "$path")"
  [ $((8#$mode & 077)) -eq 0 ] || fail "$path must be owner-only, found mode $mode"
}

load_env() {
  local line key value lineno=0
  assert_owner_only_file "$ENV_FILE"
  while IFS= read -r line || [ -n "$line" ]; do
    lineno=$((lineno + 1))
    line="${line%$'\r'}"
    case "$line" in
      ''|'#'*) continue ;;
    esac
    case "$line" in
      *[[:space:]]*) fail "env file line $lineno must be exact KEY=VALUE without shell syntax" ;;
      *=*) ;;
      *) fail "env file line $lineno must be KEY=VALUE" ;;
    esac
    key="${line%%=*}"
    value="${line#*=}"
    is_allowed_key "$key" || fail "unexpected env key: $key"
    case "$key" in
      ''|*[!A-Z0-9_]*) fail "invalid env key on line $lineno" ;;
    esac
    printf -v "$key" '%s' "$value"
    export "$key"
  done < "$ENV_FILE"
}

require_value() {
  local name="$1" value
  value="${!name:-}"
  [ -n "$value" ] || fail "$name is required"
}

reject_descriptor() {
  local name="$1" value
  value="${!name:-}"
  require_value "$name"
  case "$value" in
    *REPLACE*|*PLACEHOLDER*|*DESCRIPTOR*|*example*|*changeme*|*CHANGE_ME*)
      fail "$name still contains a descriptor"
      ;;
  esac
}

canonical_path() {
  local name="$1" value parent base parent_real
  value="${!name:-}"
  require_value "$name"
  case "$value" in
    /*) ;;
    *) fail "$name must be an absolute path" ;;
  esac
  case "$value" in
    /|/tmp|/var|/srv|/home|/home/*/.ssh|*Skeleton*|*skeleton*private*|*private*memory*|*private[_-]memory*)
      fail "$name is too broad or references forbidden private-memory scope: $value"
      ;;
  esac
  if [ -e "$value" ]; then
    readlink -f "$value"
  else
    parent="$(dirname "$value")"
    base="$(basename "$value")"
    [ -d "$parent" ] || fail "$name parent directory does not exist: $parent"
    parent_real="$(readlink -f "$parent")"
    printf '%s/%s\n' "$parent_real" "$base"
  fi
}

assert_owner_only_dir() {
  local name="$1" path="$2" mode uid gid
  if [ "$DRY_RUN" -eq 1 ] && [ ! -e "$path" ]; then
    return 0
  fi
  [ -d "$path" ] || fail "$name directory does not exist: $path"
  [ ! -L "$path" ] || fail "$name must not be a symlink: $path"
  mode="$(stat -c '%a' "$path")"
  uid="$(stat -c '%u' "$path")"
  gid="$(stat -c '%g' "$path")"
  [ "$mode" = "700" ] || fail "$name must be owner-only mode 700, found $mode"
  [ "$uid:$gid" = "1000:1000" ] || fail "$name must be owned by 1000:1000, found $uid:$gid"
}

assert_non_overlap() {
  local a="$1" b="$2" an bn
  an="${a%/}"
  bn="${b%/}"
  [ "$an" != "$bn" ] || fail "N8N_DATA_DIR and N8N_BACKUP_DIR must differ"
  case "$bn/" in "$an"/*) fail "N8N_BACKUP_DIR must not be inside N8N_DATA_DIR" ;; esac
  case "$an/" in "$bn"/*) fail "N8N_DATA_DIR must not be inside N8N_BACKUP_DIR" ;; esac
}

load_env
reject_descriptor N8N_ENCRYPTION_KEY
[ "${#N8N_ENCRYPTION_KEY}" -ge 32 ] || fail "N8N_ENCRYPTION_KEY must be at least 32 characters"
DATA_REAL="$(canonical_path N8N_DATA_DIR)"
BACKUP_REAL="$(canonical_path N8N_BACKUP_DIR)"
assert_non_overlap "$DATA_REAL" "$BACKUP_REAL"
assert_owner_only_dir N8N_DATA_DIR "$DATA_REAL"
assert_owner_only_dir N8N_BACKUP_DIR "$BACKUP_REAL"

grep -Fq "image: ${IMAGE_PIN}" "$COMPOSE_FILE" || fail "compose image is not the reviewed version and digest pin"
grep -Fq 'DB_SQLITE_DATABASE: /home/node/.n8n/database.sqlite' "$COMPOSE_FILE" || fail "compose must use n8n database.sqlite"
grep -Fq 'DB_SQLITE_POOL_SIZE: "2"' "$COMPOSE_FILE" || fail "DB_SQLITE_POOL_SIZE must be explicit and nonzero"
grep -Fq '127.0.0.1:${N8N_EDITOR_PORT:-5678}:5678' "$COMPOSE_FILE" || fail "editor bind must default to loopback"
grep -Fq 'read_only: true' "$COMPOSE_FILE" || fail "container root filesystem must be read-only"
grep -Fq 'no-new-privileges:true' "$COMPOSE_FILE" || fail "container must set no-new-privileges"
grep -Fq -- '- ALL' "$COMPOSE_FILE" || fail "container must drop all capabilities"

if command -v docker >/dev/null 2>&1 && [ "$DRY_RUN" -eq 0 ]; then
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config >/dev/null
fi

printf 'n8n deployment package validation passed%s\n' "$([ "$DRY_RUN" -eq 1 ] && printf ' (dry-run)')"
