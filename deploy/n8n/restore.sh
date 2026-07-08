#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/.env}"
COMPOSE_FILE="${SCRIPT_DIR}/compose.yaml"
DRY_RUN=0
ARCHIVE=""
IMAGE_PIN="n8nio/n8n:2.29.7@sha256:e0264b531fb97c68ece58a650173bd981f1663947281013f4a46749c15a8abc5"
HEALTH_TIMEOUT_SECONDS="${N8N_MAINTENANCE_HEALTH_TIMEOUT_SECONDS:-120}"
HEALTH_POLL_SECONDS="${N8N_MAINTENANCE_HEALTH_POLL_SECONDS:-2}"

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

validate_archive_checksum() {
  local sidecar="${ARCHIVE}.sha256" expected named extra actual mode
  [ -f "$ARCHIVE" ] && [ ! -L "$ARCHIVE" ] || fail "archive must be a regular non-symlink file"
  [ -f "$sidecar" ] && [ ! -L "$sidecar" ] || fail "checksum sidecar must be a regular non-symlink file"
  mode="$(stat -c '%a' "$sidecar")"
  [ $((8#$mode & 077)) -eq 0 ] || fail "checksum sidecar must be owner-only"
  [ "$(wc -l < "$sidecar")" -eq 1 ] || fail "archive checksum sidecar must contain one line"
  IFS=' ' read -r expected named extra < "$sidecar" || fail "invalid archive checksum sidecar"
  case "$expected" in ''|*[!0-9a-f]* ) fail "invalid archive checksum digest" ;; esac
  [ "${#expected}" -eq 64 ] || fail "invalid archive checksum digest"
  [ -z "${extra:-}" ] || fail "archive checksum sidecar has extra fields"
  [ -n "${named:-}" ] || fail "archive checksum sidecar must name the archive"
  named="${named#\*}"
  [ "$(basename "$named")" = "$(basename "$ARCHIVE")" ] || fail "archive checksum sidecar names a different archive"
  actual="$(file_sha "$ARCHIVE")"
  [ "$expected" = "$actual" ] || fail "archive checksum mismatch"
}

extract_archive_safely() {
  local dest="$1"
  python3 - "$ARCHIVE" "$dest" <<'PY'
import os, shutil, stat, sys, tarfile

archive, dest = sys.argv[1], sys.argv[2]
allowed = {"database.sqlite", "database.sqlite-wal", "database.sqlite-shm", "manifest.sha256"}
required = {"database.sqlite", "manifest.sha256"}
seen = set()
limits = {"manifest.sha256": 64 * 1024}
def limit_for(name: str) -> int:
    return limits.get(name, 2 * 1024 * 1024 * 1024)

try:
    with tarfile.open(archive, "r:gz") as tf:
        for member in tf.getmembers():
            name = member.name
            norm = os.path.normpath(name)
            if name.startswith("/") or norm.startswith("../") or norm in {".", ".."} or "/" in norm:
                raise SystemExit(f"unsafe archive path: {name}")
            if norm not in allowed:
                raise SystemExit(f"unexpected archive entry: {name}")
            if norm in seen:
                raise SystemExit(f"duplicate archive entry: {name}")
            if not member.isfile() or member.issym() or member.islnk() or stat.S_ISCHR(member.mode) or stat.S_ISBLK(member.mode) or stat.S_ISFIFO(member.mode):
                raise SystemExit(f"archive entry must be a regular file: {name}")
            if member.size < 0 or member.size > limit_for(norm):
                raise SystemExit(f"archive entry has invalid size: {name}")
            seen.add(norm)
        missing = required - seen
        if missing:
            raise SystemExit(f"archive missing required entries: {', '.join(sorted(missing))}")
        for member in tf.getmembers():
            norm = os.path.normpath(member.name)
            source = tf.extractfile(member)
            if source is None:
                raise SystemExit(f"cannot read archive entry: {member.name}")
            target = os.path.join(dest, norm)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            with os.fdopen(fd, "wb") as out:
                shutil.copyfileobj(source, out)
except (tarfile.TarError, OSError) as exc:
    raise SystemExit(f"invalid archive: {exc}")
PY
}

validate_manifest_shape() {
  python3 - "$STAGE_DIR/manifest.sha256" "$STAGE_DIR" <<'PY'
import pathlib, re, sys

manifest = pathlib.Path(sys.argv[1])
stage = pathlib.Path(sys.argv[2])
allowed = {
    "format", "image", "compose_sha256", "env_config_sha256",
    "key_fingerprint_sha256", "database.sqlite_sha256",
    "database.sqlite-wal_sha256", "database.sqlite-shm_sha256",
}
required = {
    "format", "image", "compose_sha256", "env_config_sha256",
    "key_fingerprint_sha256", "database.sqlite_sha256",
}
values = {}
for lineno, raw in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
    if not raw or "=" not in raw:
        raise SystemExit(f"malformed manifest line: {lineno}")
    key, value = raw.split("=", 1)
    if key not in allowed:
        raise SystemExit(f"unexpected manifest key: {key}")
    if key in values:
        raise SystemExit(f"duplicate manifest key: {key}")
    if not value:
        raise SystemExit(f"manifest key has empty value: {key}")
    values[key] = value
missing = required - values.keys()
if missing:
    raise SystemExit(f"manifest missing required key: {sorted(missing)[0]}")
for key in required | {k for k in values if k.endswith("_sha256")}:
    if key.endswith("_sha256") and not re.fullmatch(r"[0-9a-f]{64}", values[key]):
        raise SystemExit(f"manifest checksum is malformed: {key}")
for filename in ("database.sqlite-wal", "database.sqlite-shm"):
    key = f"{filename}_sha256"
    if (stage / filename).is_file() != (key in values):
        raise SystemExit(f"manifest/file mismatch: {filename}")
PY
}

manifest_value() {
  local key="$1"
  awk -F= -v key="$key" '$1 == key { print substr($0, length($1) + 2); found=1 } END { exit found ? 0 : 1 }' "$STAGE_DIR/manifest.sha256"
}

validate_manifest_values() {
  local file expected actual
  validate_manifest_shape
  [ "$(manifest_value format)" = "n8n-sqlite-backup-v1" ] || fail "unsupported backup manifest format"
  [ "$(manifest_value image)" = "$IMAGE_PIN" ] || fail "backup image pin does not match compose image"
  [ "$(manifest_value compose_sha256)" = "$(file_sha "$COMPOSE_FILE")" ] || fail "compose config hash mismatch"
  [ "$(manifest_value env_config_sha256)" = "$(redacted_env_sha)" ] || fail "environment config hash mismatch"
  [ "$(manifest_value key_fingerprint_sha256)" = "$(key_fingerprint)" ] || fail "encryption key fingerprint mismatch"
  for file in database.sqlite database.sqlite-wal database.sqlite-shm; do
    [ -f "$STAGE_DIR/$file" ] || continue
    expected="$(manifest_value "${file}_sha256")"
    actual="$(file_sha "$STAGE_DIR/$file")"
    [ "$expected" = "$actual" ] || fail "$file checksum mismatch"
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

install_staged_files() {
  local file
  rm -f "$DATA_DIR/database.sqlite.new" "$DATA_DIR/database.sqlite-wal.new" "$DATA_DIR/database.sqlite-shm.new"
  install -m 600 -o 1000 -g 1000 "$STAGE_DIR/database.sqlite" "$DATA_DIR/database.sqlite.new"
  for file in database.sqlite-wal database.sqlite-shm; do
    if [ -f "$STAGE_DIR/$file" ]; then
      install -m 600 -o 1000 -g 1000 "$STAGE_DIR/$file" "$DATA_DIR/$file.new"
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

WORK_DIR=""
STAGE_DIR=""
SNAPSHOT_DIR=""
running_before=0
state_captured=0
snapshot_ready=0
replacement_started=0
success=0

cleanup() {
  local status=$? recovery_failed=0
  trap - EXIT INT TERM HUP
  set +e
  if [ -n "${DATA_DIR:-}" ]; then
    rm -f "$DATA_DIR/database.sqlite.new" "$DATA_DIR/database.sqlite-wal.new" "$DATA_DIR/database.sqlite-shm.new"
  fi
  if [ "$success" -ne 1 ]; then
    if [ "$replacement_started" -eq 1 ] && [ "$snapshot_ready" -eq 1 ]; then
      if compose_running; then
        docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" stop n8n >/dev/null 2>&1 || recovery_failed=1
      fi
      restore_snapshot "$SNAPSHOT_DIR" || recovery_failed=1
    fi
    if [ "$running_before" -eq 1 ]; then
      docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d n8n >/dev/null 2>&1 || recovery_failed=1
      wait_healthy || recovery_failed=1
    elif [ "$state_captured" -eq 1 ] && compose_running; then
      docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" stop n8n >/dev/null 2>&1 || recovery_failed=1
    fi
  fi
  [ -z "$WORK_DIR" ] || rm -rf "$WORK_DIR"
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
extract_archive_safely "$STAGE_DIR"
validate_manifest_values
sqlite_integrity_check "$STAGE_DIR/database.sqlite" || fail "restored database failed PRAGMA integrity_check"

if [ "$DRY_RUN" -eq 1 ]; then
  success=1
  printf 'dry-run: archive, manifest, config hashes, key fingerprint and SQLite integrity validated for %s\n' "$ARCHIVE"
  exit 0
fi

if compose_running; then running_before=1; fi
state_captured=1
if [ "$running_before" -eq 1 ]; then
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" stop n8n >/dev/null
fi
snapshot_current "$SNAPSHOT_DIR"
snapshot_ready=1
install_staged_files
replacement_started=1
commit_replacement
sqlite_integrity_check "$DATA_DIR/database.sqlite" || fail "replacement database failed PRAGMA integrity_check"

if [ "$running_before" -eq 1 ]; then
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d n8n >/dev/null
  wait_healthy || fail "n8n did not become healthy after restore"
fi

success=1
printf 'restored %s\n' "$ARCHIVE"
