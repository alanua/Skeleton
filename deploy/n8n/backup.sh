#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/.env}"
COMPOSE_FILE="${SCRIPT_DIR}/compose.yaml"
DRY_RUN=0
IMAGE_PIN="n8nio/n8n:2.29.7@sha256:e0264b531fb97c68ece58a650173bd981f1663947281013f4a46749c15a8abc5"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --env-file) ENV_FILE="${2:?missing env file path}"; shift 2 ;;
    *) printf 'usage: %s [--dry-run] [--env-file PATH]\n' "$0" >&2; exit 64 ;;
  esac
done

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

is_allowed_key() {
  case "$1" in
    COMPOSE_PROJECT_NAME|N8N_COMPOSE_PROJECT_NAME|N8N_EDITOR_PORT|N8N_HOST|N8N_DATA_DIR|N8N_BACKUP_DIR|N8N_ENCRYPTION_KEY) return 0 ;;
    *) return 1 ;;
  esac
}

load_env() {
  local line key value
  [ -f "$ENV_FILE" ] || fail "missing env file: $ENV_FILE"
  [ $((8#$(stat -c '%a' "$ENV_FILE") & 077)) -eq 0 ] || fail "$ENV_FILE must be owner-only"
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in ''|'#'*) continue ;; esac
    case "$line" in *[[:space:]]*) fail "env file must contain only exact KEY=VALUE lines" ;; *=*) ;; *) fail "env file must contain only KEY=VALUE lines" ;; esac
    key="${line%%=*}"
    value="${line#*=}"
    is_allowed_key "$key" || fail "unexpected env key: $key"
    printf -v "$key" '%s' "$value"
    export "$key"
  done < "$ENV_FILE"
}

canonical_existing_dir() {
  local name="$1" value real mode uid gid
  value="${!name:-}"
  [ -n "$value" ] || fail "$name is required"
  case "$value" in /*) ;; *) fail "$name must be absolute" ;; esac
  real="$(readlink -f "$value")"
  [ -d "$real" ] || fail "$name directory does not exist: $real"
  [ ! -L "$value" ] || fail "$name must not be a symlink: $value"
  mode="$(stat -c '%a' "$real")"
  uid="$(stat -c '%u' "$real")"
  gid="$(stat -c '%g' "$real")"
  [ "$mode" = "700" ] || fail "$name must be mode 700, found $mode"
  [ "$uid:$gid" = "1000:1000" ] || fail "$name must be owned by 1000:1000, found $uid:$gid"
  printf '%s\n' "$real"
}

key_fingerprint() {
  printf '%s' "$N8N_ENCRYPTION_KEY" | sha256sum | awk '{print $1}'
}

file_sha() {
  sha256sum "$1" | awk '{print $1}'
}

sqlite_integrity_check() {
  local db="$1"
  python3 - "$db" <<'PY'
import sqlite3, sys
db = sys.argv[1]
try:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    row = conn.execute("PRAGMA integrity_check").fetchone()
    conn.close()
except sqlite3.DatabaseError:
    sys.exit(1)
sys.exit(0 if row and row[0] == "ok" else 1)
PY
}

compose_running() {
  [ -n "$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps --status running -q n8n 2>/dev/null || true)" ]
}

load_env
validate_args=(--env-file "$ENV_FILE")
if [ "$DRY_RUN" -eq 1 ]; then
  validate_args+=(--dry-run)
fi
"${SCRIPT_DIR}/validate.sh" "${validate_args[@]}" >/dev/null
[ "${#N8N_ENCRYPTION_KEY}" -ge 32 ] || fail "N8N_ENCRYPTION_KEY must be at least 32 characters"
DATA_DIR="$(canonical_existing_dir N8N_DATA_DIR)"
BACKUP_DIR="$(canonical_existing_dir N8N_BACKUP_DIR)"
[ "$DATA_DIR" != "$BACKUP_DIR" ] || fail "backup directory must differ from data directory"
case "$BACKUP_DIR/" in "$DATA_DIR"/*) fail "backup directory must not be inside data directory" ;; esac

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="${BACKUP_DIR}/n8n-sqlite-${timestamp}.tar.gz"

if [ "$DRY_RUN" -eq 1 ]; then
  printf 'dry-run: would preserve current n8n running state, archive exact SQLite files and manifest to %s\n' "$archive"
  exit 0
fi

running_before=0
if compose_running; then
  running_before=1
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" stop n8n >/dev/null
fi

tmpdir="$(mktemp -d "${BACKUP_DIR}/.backup-stage.XXXXXX")"
cleanup() {
  rm -rf "$tmpdir"
  if [ "$running_before" -eq 1 ]; then
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d n8n >/dev/null
  fi
}
trap cleanup EXIT

[ -f "${DATA_DIR}/database.sqlite" ] || fail "missing ${DATA_DIR}/database.sqlite"
sqlite_integrity_check "${DATA_DIR}/database.sqlite" || fail "database.sqlite failed PRAGMA integrity_check"

for file in database.sqlite database.sqlite-wal database.sqlite-shm; do
  if [ -e "${DATA_DIR}/${file}" ]; then
    [ -f "${DATA_DIR}/${file}" ] && [ ! -L "${DATA_DIR}/${file}" ] || fail "unexpected non-regular SQLite file: ${file}"
    install -m 600 -o 1000 -g 1000 "${DATA_DIR}/${file}" "${tmpdir}/${file}"
  fi
done

{
  printf 'format=n8n-sqlite-backup-v1\n'
  printf 'image=%s\n' "$IMAGE_PIN"
  printf 'compose_sha256=%s\n' "$(file_sha "$COMPOSE_FILE")"
  printf 'env_config_sha256=%s\n' "$(sed 's/^N8N_ENCRYPTION_KEY=.*/N8N_ENCRYPTION_KEY=<redacted>/' "$ENV_FILE" | sha256sum | awk '{print $1}')"
  printf 'key_fingerprint_sha256=%s\n' "$(key_fingerprint)"
  for file in database.sqlite database.sqlite-wal database.sqlite-shm; do
    [ -f "${tmpdir}/${file}" ] && printf '%s_sha256=%s\n' "$file" "$(file_sha "${tmpdir}/${file}")"
  done
} > "${tmpdir}/manifest.sha256"
chmod 600 "${tmpdir}/manifest.sha256"

tar -C "$tmpdir" -czf "$archive" database.sqlite manifest.sha256 $(for file in database.sqlite-wal database.sqlite-shm; do [ -f "${tmpdir}/${file}" ] && printf '%s ' "$file"; done)
chmod 600 "$archive"
sha256sum "$archive" > "${archive}.sha256"
chmod 600 "${archive}.sha256"
printf 'created %s\n' "$archive"
