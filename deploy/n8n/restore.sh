#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/.env}"
COMPOSE_FILE="${SCRIPT_DIR}/compose.yaml"
DRY_RUN=0
ARCHIVE=""
IMAGE_PIN="n8nio/n8n:2.29.7@sha256:e0264b531fb97c68ece58a650173bd981f1663947281013f4a46749c15a8abc5"

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
case "$ARCHIVE" in /*) ;; *) fail "archive path must be absolute" ;; esac

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

validate_archive_checksum() {
  local sidecar="${ARCHIVE}.sha256" expected actual sidecar_archive
  [ -f "$sidecar" ] || fail "missing archive checksum sidecar: $sidecar"
  read -r expected sidecar_archive < "$sidecar"
  actual="$(file_sha "$ARCHIVE")"
  [ "$expected" = "$actual" ] || fail "archive checksum mismatch"
  [ -z "${sidecar_archive:-}" ] || [ "$(basename "$sidecar_archive")" = "$(basename "$ARCHIVE")" ] || fail "archive checksum sidecar names a different archive"
}

extract_archive_safely() {
  local dest="$1"
  python3 - "$ARCHIVE" "$dest" <<'PY'
import os, stat, sys, tarfile

archive, dest = sys.argv[1], sys.argv[2]
expected = {"database.sqlite", "database.sqlite-wal", "database.sqlite-shm", "manifest.sha256"}
required = {"database.sqlite", "manifest.sha256"}
seen = set()

try:
    with tarfile.open(archive, "r:gz") as tf:
        for member in tf.getmembers():
            name = member.name
            norm = os.path.normpath(name)
            if name.startswith("/") or norm.startswith("../") or norm in {".", ".."} or "/" in norm:
                raise SystemExit(f"unsafe archive path: {name}")
            if norm not in expected:
                raise SystemExit(f"unexpected archive entry: {name}")
            if not member.isfile():
                raise SystemExit(f"archive entry must be a regular file: {name}")
            if member.issym() or member.islnk() or stat.S_ISCHR(member.mode) or stat.S_ISBLK(member.mode) or stat.S_ISFIFO(member.mode):
                raise SystemExit(f"archive entry has unsafe type: {name}")
            seen.add(norm)
        missing = required - seen
        if missing:
            raise SystemExit(f"archive missing required entries: {', '.join(sorted(missing))}")
        for member in tf.getmembers():
            member.name = os.path.normpath(member.name)
            tf.extract(member, dest)
except (tarfile.TarError, OSError) as exc:
    raise SystemExit(f"invalid archive: {exc}")
PY
}

manifest_value() {
  local key="$1"
  awk -F= -v key="$key" '$1 == key { print substr($0, length($1) + 2); found=1 } END { exit found ? 0 : 1 }' "$STAGE_DIR/manifest.sha256"
}

validate_manifest() {
  local key value expected actual file
  while IFS='=' read -r key value; do
    case "$key" in
      format|image|compose_sha256|env_config_sha256|key_fingerprint_sha256|database.sqlite_sha256|database.sqlite-wal_sha256|database.sqlite-shm_sha256) ;;
      *) fail "unexpected manifest key: $key" ;;
    esac
    [ -n "$value" ] || fail "manifest key has empty value: $key"
  done < "$STAGE_DIR/manifest.sha256"

  [ "$(manifest_value format)" = "n8n-sqlite-backup-v1" ] || fail "unsupported backup manifest format"
  [ "$(manifest_value image)" = "$IMAGE_PIN" ] || fail "backup image pin does not match compose image"
  [ "$(manifest_value key_fingerprint_sha256)" = "$(key_fingerprint)" ] || fail "encryption key fingerprint mismatch"
  [ "$(manifest_value database.sqlite_sha256)" = "$(file_sha "$STAGE_DIR/database.sqlite")" ] || fail "database.sqlite checksum mismatch"
  for file in database.sqlite-wal database.sqlite-shm; do
    if [ -f "$STAGE_DIR/$file" ]; then
      expected="$(manifest_value "${file}_sha256")" || fail "manifest missing checksum for $file"
      actual="$(file_sha "$STAGE_DIR/$file")"
      [ "$expected" = "$actual" ] || fail "$file checksum mismatch"
    elif manifest_value "${file}_sha256" >/dev/null 2>&1; then
      fail "manifest includes checksum for missing $file"
    fi
  done
}

install_staged_files() {
  local dest="$1"
  install -m 600 -o 1000 -g 1000 "$STAGE_DIR/database.sqlite" "$dest/database.sqlite.new"
  for file in database.sqlite-wal database.sqlite-shm; do
    if [ -f "$STAGE_DIR/$file" ]; then
      install -m 600 -o 1000 -g 1000 "$STAGE_DIR/$file" "$dest/$file.new"
    fi
  done
}

snapshot_current() {
  local dest="$1" file
  mkdir -p "$dest"
  for file in database.sqlite database.sqlite-wal database.sqlite-shm; do
    if [ -e "$DATA_DIR/$file" ]; then
      [ -f "$DATA_DIR/$file" ] && [ ! -L "$DATA_DIR/$file" ] || fail "current data file is not regular: $file"
      install -m 600 -o 1000 -g 1000 "$DATA_DIR/$file" "$dest/$file"
    fi
  done
}

restore_snapshot() {
  local src="$1" file
  for file in database.sqlite database.sqlite-wal database.sqlite-shm; do
    rm -f "$DATA_DIR/$file" "$DATA_DIR/$file.new"
    if [ -f "$src/$file" ]; then
      install -m 600 -o 1000 -g 1000 "$src/$file" "$DATA_DIR/$file"
    fi
  done
}

commit_replacement() {
  local file
  mv -f "$DATA_DIR/database.sqlite.new" "$DATA_DIR/database.sqlite"
  for file in database.sqlite-wal database.sqlite-shm; do
    if [ -f "$DATA_DIR/$file.new" ]; then
      mv -f "$DATA_DIR/$file.new" "$DATA_DIR/$file"
    else
      rm -f "$DATA_DIR/$file"
    fi
  done
}

load_env
if [ "$DRY_RUN" -eq 1 ]; then
  "${SCRIPT_DIR}/validate.sh" --dry-run --env-file "$ENV_FILE" >/dev/null
  printf 'dry-run: would validate archive checksum, manifest, key fingerprint, SQLite integrity, replace files atomically, and preserve n8n running state for %s\n' "$ARCHIVE"
  exit 0
fi

"${SCRIPT_DIR}/validate.sh" --env-file "$ENV_FILE" >/dev/null
[ -f "$ARCHIVE" ] || fail "archive does not exist: $ARCHIVE"
DATA_DIR="$(canonical_existing_dir N8N_DATA_DIR)"
BACKUP_DIR="$(canonical_existing_dir N8N_BACKUP_DIR)"
ARCHIVE_REAL="$(readlink -f "$ARCHIVE")"
case "$ARCHIVE_REAL" in "$DATA_DIR"|"$DATA_DIR"/*) fail "archive must not be inside N8N_DATA_DIR" ;; esac

validate_archive_checksum
WORK_DIR="$(mktemp -d "${BACKUP_DIR}/.restore.XXXXXX")"
STAGE_DIR="${WORK_DIR}/stage"
SNAPSHOT_DIR="${WORK_DIR}/emergency-current"
mkdir -p "$STAGE_DIR"
trap 'rm -rf "$WORK_DIR"' EXIT

extract_archive_safely "$STAGE_DIR"
validate_manifest
sqlite_integrity_check "$STAGE_DIR/database.sqlite" || fail "restored database failed PRAGMA integrity_check"
install_staged_files "$DATA_DIR"

running_before=0
if compose_running; then
  running_before=1
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" stop n8n >/dev/null
fi

snapshot_current "$SNAPSHOT_DIR"
if ! commit_replacement || ! sqlite_integrity_check "$DATA_DIR/database.sqlite"; then
  restore_snapshot "$SNAPSHOT_DIR"
  [ "$running_before" -eq 1 ] && docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d n8n >/dev/null
  fail "replacement failed; restored emergency rollback point"
fi

if [ "$running_before" -eq 1 ]; then
  if ! docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d n8n >/dev/null; then
    restore_snapshot "$SNAPSHOT_DIR"
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d n8n >/dev/null || true
    fail "startup validation failed; restored emergency rollback point"
  fi
fi

printf 'restored %s\n' "$ARCHIVE"
