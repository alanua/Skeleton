#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/compose.yaml"
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/.env}"
DRY_RUN=0
IMAGE_PIN="n8nio/n8n:2.29.7@sha256:e0264b531fb97c68ece58a650173bd981f1663947281013f4a46749c15a8abc5"
NODES_EXCLUDE='["n8n-nodes-base.code","n8n-nodes-base.executeCommand","n8n-nodes-base.ssh","n8n-nodes-base.localFileTrigger","n8n-nodes-base.readWriteFile","n8n-nodes-base.readBinaryFile","n8n-nodes-base.readBinaryFiles","n8n-nodes-base.writeBinaryFile"]'

usage() { printf 'usage: %s [--dry-run] [--env-file PATH]\n' "$0"; }
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --env-file) [ "$#" -ge 2 ] || { usage >&2; exit 64; }; ENV_FILE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 64 ;;
  esac
done

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

is_allowed_key() {
  case "$1" in
    COMPOSE_PROJECT_NAME|N8N_COMPOSE_PROJECT_NAME|N8N_EDITOR_PORT|N8N_HOST|N8N_DATA_DIR|N8N_BACKUP_DIR|N8N_ENCRYPTION_KEY) return 0 ;;
    *) return 1 ;;
  esac
}

assert_owner_only_file() {
  local path="$1" mode
  [ -f "$path" ] && [ ! -L "$path" ] || fail "missing or unsafe env file: $path"
  mode="$(stat -c '%a' "$path")"
  [ $((8#$mode & 077)) -eq 0 ] || fail "$path must be owner-only, found mode $mode"
}

load_env() {
  local line key value lineno=0
  declare -A seen=()
  assert_owner_only_file "$ENV_FILE"
  while IFS= read -r line || [ -n "$line" ]; do
    lineno=$((lineno + 1))
    line="${line%$'\r'}"
    case "$line" in ''|'#'*) continue ;; esac
    case "$line" in
      *[[:space:]]*) fail "env file line $lineno must be exact KEY=VALUE without shell syntax" ;;
      *=*) ;;
      *) fail "env file line $lineno must be KEY=VALUE" ;;
    esac
    key="${line%%=*}"
    value="${line#*=}"
    is_allowed_key "$key" || fail "unexpected env key: $key"
    case "$key" in ''|*[!A-Z0-9_]*) fail "invalid env key on line $lineno" ;; esac
    [ -z "${seen[$key]:-}" ] || fail "duplicate env key: $key"
    seen[$key]=1
    printf -v "$key" '%s' "$value"
    export "$key"
  done < "$ENV_FILE"
}

require_value() {
  local name="$1" value="${!1:-}"
  [ -n "$value" ] || fail "$name is required"
}

reject_descriptor() {
  local name="$1" value="${!1:-}"
  require_value "$name"
  case "$value" in
    *REPLACE*|*PLACEHOLDER*|*DESCRIPTOR*|*example*|*changeme*|*CHANGE_ME*) fail "$name still contains a descriptor" ;;
  esac
}

canonical_path() {
  local name="$1" value parent base parent_real
  value="${!name:-}"
  require_value "$name"
  case "$value" in /*) ;; *) fail "$name must be an absolute path" ;; esac
  case "$value" in
    /|/tmp|/var|/srv|/home|/home/*/.ssh|*Skeleton*|*skeleton*private*|*private*memory*|*private[_-]memory*)
      fail "$name is too broad or references forbidden private-memory scope: $value" ;;
  esac
  [ ! -L "$value" ] || fail "$name must not be a symlink: $value"
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
  if [ "$DRY_RUN" -eq 1 ] && [ ! -e "$path" ]; then return 0; fi
  [ -d "$path" ] && [ ! -L "$path" ] || fail "$name must be a regular directory: $path"
  mode="$(stat -c '%a' "$path")"
  uid="$(stat -c '%u' "$path")"
  gid="$(stat -c '%g' "$path")"
  [ "$mode" = "700" ] || fail "$name must be owner-only mode 700, found $mode"
  [ "$uid:$gid" = "1000:1000" ] || fail "$name must be owned by 1000:1000, found $uid:$gid"
}

assert_non_overlap() {
  local an="${1%/}" bn="${2%/}"
  [ "$an" != "$bn" ] || fail "N8N_DATA_DIR and N8N_BACKUP_DIR must differ"
  case "$bn/" in "$an"/*) fail "N8N_BACKUP_DIR must not be inside N8N_DATA_DIR" ;; esac
  case "$an/" in "$bn"/*) fail "N8N_DATA_DIR must not be inside N8N_BACKUP_DIR" ;; esac
}

require_compose_line() {
  grep -Fq -- "$1" "$COMPOSE_FILE" || fail "$2"
}

load_env
reject_descriptor N8N_ENCRYPTION_KEY
[ "${#N8N_ENCRYPTION_KEY}" -ge 32 ] || fail "N8N_ENCRYPTION_KEY must be at least 32 characters"
DATA_REAL="$(canonical_path N8N_DATA_DIR)"
BACKUP_REAL="$(canonical_path N8N_BACKUP_DIR)"
assert_non_overlap "$DATA_REAL" "$BACKUP_REAL"
assert_owner_only_dir N8N_DATA_DIR "$DATA_REAL"
assert_owner_only_dir N8N_BACKUP_DIR "$BACKUP_REAL"

require_compose_line "image: ${IMAGE_PIN}" "compose image is not the reviewed version and digest pin"
require_compose_line 'DB_SQLITE_DATABASE: /home/node/.n8n/database.sqlite' "compose must use n8n database.sqlite"
require_compose_line 'DB_SQLITE_POOL_SIZE: "2"' "DB_SQLITE_POOL_SIZE must be explicit and nonzero"
require_compose_line '127.0.0.1:${N8N_EDITOR_PORT:-5678}:5678' "editor bind must default to loopback"
require_compose_line 'read_only: true' "container root filesystem must be read-only"
require_compose_line 'no-new-privileges:true' "container must set no-new-privileges"
require_compose_line '- ALL' "container must drop all capabilities"
require_compose_line 'N8N_RUNNERS_MODE: internal' "task runner must use explicit internal mode"
require_compose_line 'N8N_RUNNERS_INSECURE_MODE: "false"' "task runner insecure mode must be disabled"
require_compose_line 'N8N_RUNNERS_BROKER_LISTEN_ADDRESS: 127.0.0.1' "task runner broker must remain loopback-only"
require_compose_line 'N8N_RUNNERS_MAX_CONCURRENCY: "1"' "task runner concurrency must be bounded"
require_compose_line 'N8N_RUNNERS_TASK_TIMEOUT: "60"' "task runner timeout must be bounded"
require_compose_line 'N8N_BLOCK_ENV_ACCESS_IN_NODE: "true"' "workflow environment access must be blocked"
require_compose_line 'N8N_BLOCK_FILE_ACCESS_TO_N8N_FILES: "true"' "n8n-owned file access must be blocked"
require_compose_line 'N8N_RESTRICT_FILE_ACCESS_TO: /home/node/.n8n-files-disabled' "workflow file access must target an unavailable path"
require_compose_line 'N8N_PYTHON_ENABLED: "false"' "Python execution must be disabled"
require_compose_line "NODES_EXCLUDE: '$NODES_EXCLUDE'" "dangerous node exclusions must match the verified n8n 2.29.7 identifiers"
require_compose_line 'N8N_COMMUNITY_PACKAGES_ENABLED: "false"' "community packages must be disabled"

! grep -Fq 'N8N_RUNNERS_ENABLED' "$COMPOSE_FILE" || fail "deprecated N8N_RUNNERS_ENABLED must not be used on n8n 2.29.7"
! grep -Fq 'N8N_RUNNERS_MODE: external' "$COMPOSE_FILE" || fail "external task runner mode is forbidden"
! grep -Fq 'N8N_RUNNERS_AUTH_TOKEN' "$COMPOSE_FILE" || fail "external task runner credentials are forbidden"
! grep -Fq '/var/run/docker.sock' "$COMPOSE_FILE" || fail "Docker socket mount is forbidden"

if command -v docker >/dev/null 2>&1 && [ "$DRY_RUN" -eq 0 ]; then
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config >/dev/null
fi

printf 'n8n deployment package validation passed%s\n' "$([ "$DRY_RUN" -eq 1 ] && printf ' (dry-run)')"
