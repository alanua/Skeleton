#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/.env}"
COMPOSE_FILE="${SCRIPT_DIR}/compose.yaml"
DRY_RUN=0
IMAGE_PIN="n8nio/n8n:2.29.7@sha256:e0264b531fb97c68ece58a650173bd981f1663947281013f4a46749c15a8abc5"
HEALTH_TIMEOUT_SECONDS="${N8N_MAINTENANCE_HEALTH_TIMEOUT_SECONDS:-120}"
HEALTH_POLL_SECONDS="${N8N_MAINTENANCE_HEALTH_POLL_SECONDS:-2}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --env-file) ENV_FILE="${2:?missing env file path}"; shift 2 ;;
    *) printf 'usage: %s [--dry-run] [--env-file PATH]\n' "$0" >&2; exit 64 ;;
  esac
done

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
case "$HEALTH_TIMEOUT_SECONDS:$HEALTH_POLL_SECONDS" in
  *[!0-9:]*|:*|*:) fail "health timeout settings must be positive integers" ;;
esac
[ "$HEALTH_TIMEOUT_SECONDS" -gt 0 ] && [ "$HEALTH_POLL_SECONDS" -gt 0 ] || fail "health timeout settings must be positive integers"

is_allowed_key() {
  case "$1" in
    COMPOSE_PROJECT_NAME|N8N_COMPOSE_PROJECT_NAME|N8N_EDITOR_PORT|N8N_HOST|N8N_DATA_DIR|N8N_BACKUP_DIR|N8N_ENCRYPTION_KEY) return 0 ;;
    *) return 1 ;;
  esac
}

load_env() {
  local line key value
  [ -f "$ENV_FILE" ] && [ ! -L "$ENV_FILE" ] || fail "missing or unsafe env file: $ENV_FILE"
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
  [ ! -L "$value" ] || fail "$name must not be a symlink: $value"
  real="$(readlink -f "$value")"
  [ -d "$real" ] || fail "$name directory does not exist: $real"
  mode="$(stat -c '%a' "$real")"
  uid="$(stat -c '%u' "$real")"
  gid="$(stat -c '%g' "$real")"
  [ "$mode" = "700" ] || fail "$name must be mode 700, found $mode"
  [ "$uid:$gid" = "1000:1000" ] || fail "$name must be owned by 1000:1000, found $uid:$gid"
  printf '%s\n' "$real"
}

key_fingerprint() { printf '%s' "$N8N_ENCRYPTION_KEY" | sha256sum | awk '{print $1}'; }
file_sha() { sha256sum "$1" | awk '{print $1}'; }
redacted_env_sha() { sed 's/^N8N_ENCRYPTION_KEY=.*/N8N_ENCRYPTION_KEY=<redacted>/' "$ENV_FILE" | sha256sum | awk '{print $1}'; }

sqlite_integrity_check() {
  local db="$1"
  python3 - "$db" <<'PY'
import sqlite3, sys
try:
    conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
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

wait_healthy() {
  local deadline container_id status
  deadline=$((SECONDS + HEALTH_TIMEOUT_SECONDS))
  while [ "$SECONDS" -lt "$deadline" ]; do
    container_id="$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps -q n8n 2>/dev/null || true)"
    if [ -n "$container_id" ]; then
      status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || true)"
      case "$status" in
        healthy) return 0 ;;
        unhealthy|exited|dead) return 1 ;;
      esac
    fi
    sleep "$HEALTH_POLL_SECONDS"
  done
  return 1
}

WORK_DIR=""
ARCHIVE_TMP=""
SIDECAR_TMP=""
running_before=0
service_mutation_started=0
success=0

cleanup() {
  local status=$? recovery_failed=0
  trap - EXIT INT TERM HUP
  set +e
  [ -z "$ARCHIVE_TMP" ] || rm -f "$ARCHIVE_TMP"
  [ -z "$SIDECAR_TMP" ] || rm -f "$SIDECAR_TMP"
  [ -z "$WORK_DIR" ] || rm -rf "$WORK_DIR"
  if [ "$success" -ne 1 ] && [ "$service_mutation_started" -eq 1 ]; then
    if [ "$running_before" -eq 1 ]; then
      docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" stop n8n >/dev/null 2>&1 || true
      docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d n8n >/dev/null 2>&1 || recovery_failed=1
      wait_healthy || recovery_failed=1
    elif compose_running; then
      docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" stop n8n >/dev/null 2>&1 || recovery_failed=1
    fi
  fi
  if [ "$status" -eq 0 ] && [ "$recovery_failed" -ne 0 ]; then status=1; fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

load_env
validate_args=(--env-file "$ENV_FILE")
if [ "$DRY_RUN" -eq 1 ]; then validate_args=(--dry-run "${validate_args[@]}"); fi
"${SCRIPT_DIR}/validate.sh" "${validate_args[@]}" >/dev/null
[ "${#N8N_ENCRYPTION_KEY}" -ge 32 ] || fail "N8N_ENCRYPTION_KEY must be at least 32 characters"
DATA_DIR="$(canonical_existing_dir N8N_DATA_DIR)"
BACKUP_DIR="$(canonical_existing_dir N8N_BACKUP_DIR)"
[ "$DATA_DIR" != "$BACKUP_DIR" ] || fail "backup directory must differ from data directory"
case "$BACKUP_DIR/" in "$DATA_DIR"/*) fail "backup directory must not be inside data directory" ;; esac

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="${BACKUP_DIR}/n8n-sqlite-${timestamp}.tar.gz"
[ ! -e "$archive" ] && [ ! -e "${archive}.sha256" ] || fail "backup output already exists"

if [ "$DRY_RUN" -eq 1 ]; then
  success=1
  printf 'dry-run: would preserve current n8n state and create a verified SQLite backup at %s\n' "$archive"
  exit 0
fi

WORK_DIR="$(mktemp -d "${BACKUP_DIR}/.backup-stage.XXXXXX")"
ARCHIVE_TMP="$(mktemp "${BACKUP_DIR}/.archive.XXXXXX.tar.gz")"
SIDECAR_TMP="$(mktemp "${BACKUP_DIR}/.archive.XXXXXX.sha256")"
chmod 600 "$ARCHIVE_TMP" "$SIDECAR_TMP"

if compose_running; then running_before=1; fi
if [ "$running_before" -eq 1 ]; then
  service_mutation_started=1
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" stop n8n >/dev/null
fi

[ -f "${DATA_DIR}/database.sqlite" ] && [ ! -L "${DATA_DIR}/database.sqlite" ] || fail "missing or unsafe database.sqlite"
sqlite_integrity_check "${DATA_DIR}/database.sqlite" || fail "database.sqlite failed PRAGMA integrity_check"

files=(database.sqlite)
for file in database.sqlite database.sqlite-wal database.sqlite-shm; do
  if [ -e "${DATA_DIR}/${file}" ]; then
    [ -f "${DATA_DIR}/${file}" ] && [ ! -L "${DATA_DIR}/${file}" ] || fail "unexpected non-regular SQLite file: ${file}"
    install -m 600 -o 1000 -g 1000 "${DATA_DIR}/${file}" "${WORK_DIR}/${file}"
    if [ "$file" != database.sqlite ]; then files+=("$file"); fi
  fi
done

{
  printf 'format=n8n-sqlite-backup-v1\n'
  printf 'image=%s\n' "$IMAGE_PIN"
  printf 'compose_sha256=%s\n' "$(file_sha "$COMPOSE_FILE")"
  printf 'env_config_sha256=%s\n' "$(redacted_env_sha)"
  printf 'key_fingerprint_sha256=%s\n' "$(key_fingerprint)"
  for file in "${files[@]}"; do
    printf '%s_sha256=%s\n' "$file" "$(file_sha "${WORK_DIR}/${file}")"
  done
} > "${WORK_DIR}/manifest.sha256"
chmod 600 "${WORK_DIR}/manifest.sha256"
chown 1000:1000 "${WORK_DIR}/manifest.sha256"
files+=(manifest.sha256)

tar -C "$WORK_DIR" -czf "$ARCHIVE_TMP" "${files[@]}"
chmod 600 "$ARCHIVE_TMP"
chown 1000:1000 "$ARCHIVE_TMP"
printf '%s  %s\n' "$(file_sha "$ARCHIVE_TMP")" "$(basename "$archive")" > "$SIDECAR_TMP"
chmod 600 "$SIDECAR_TMP"
chown 1000:1000 "$SIDECAR_TMP"

if [ "$running_before" -eq 1 ]; then
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d n8n >/dev/null
  wait_healthy || fail "n8n did not become healthy after backup"
fi

mv -f "$ARCHIVE_TMP" "$archive"
ARCHIVE_TMP=""
mv -f "$SIDECAR_TMP" "${archive}.sha256"
SIDECAR_TMP=""
success=1
printf 'created %s\n' "$archive"
