#!/usr/bin/env bash
set -Eeuo pipefail

PAYLOAD_FILE=""
BACKUP_DIR=""
STAGING_PARENT=""
COMMITTED=0
ACTIVATION_STARTED=0
BACKUPS_READY=0
HAD_INSTALL_ROOT=0
HAD_EXEC_ROOT=0
HAD_SUDOERS=0

APPROVED_SCRIPT_BLOB_SHA="d83afd466468673a68801bdf79e8e849219a338c"
APPROVED_CORE_BLOB_SHA="9af234d2fe7493db4cf8c7506dd546e5a771d5cb"
APPROVED_JOB_SCHEMA_BLOB_SHA="1f2daf9fcf9b553c067b3a84c494e626ccff9b75"
APPROVED_OBSERVATION_SCHEMA_BLOB_SHA="0e693f12ea71bf84175210480f4bfe89fe07e5d8"
APPROVED_RECEIPT_SCHEMA_BLOB_SHA="4c6a09efbb295e91740a8be54ab990ef8e4a685e"

usage() {
  cat <<'EOF'
Usage: sudo scripts/install_home_edge_esp_lab.sh < payload.json

Installs the Debian 13 Home Edge ESP Lab read-only runtime from an exact JSON
stdin payload. The installer copies only approved repository bytes into a
root-owned immutable runtime and never installs packages, opens devices, starts
services, or executes checkout Python.

Required payload:
{
  "schema": "skeleton.home_edge.esp_lab.installer.v1",
  "operation": "install_home_edge_esp_lab_runtime_v1",
  "approved_git_head": "5c99681f480f399b8b95eedc756734ebe789fc36",
  "repo_root": "/path/to/Skeleton",
  "protected_installer_path": "/usr/local/libexec/skeleton/home-edge/esp-lab-installer/install_home_edge_esp_lab.sh",
  "install_root": "/usr/local/lib/skeleton/home-edge/esp-lab",
  "exec_root": "/usr/local/libexec/skeleton/home-edge/esp-lab",
  "sudoers_path": "/etc/sudoers.d/skeleton-home-edge-esp-lab",
  "runner_user": "agent",
  "runner_service": "skeleton-runner-poll.service",
  "fake_root": false
}
EOF
}

die() {
  printf 'BLOCKED: %s\n' "$1" >&2
  exit 2
}

cleanup() {
  local rc=$?
  trap - EXIT
  rm -f "$PAYLOAD_FILE"
  rm -rf "$STAGING_PARENT" "${INSTALL_ROOT:-}.new" "${EXEC_ROOT:-}.new"
  if [[ $COMMITTED -eq 0 && $ACTIVATION_STARTED -eq 1 ]]; then
    if [[ $BACKUPS_READY -ne 1 ]]; then
      printf 'FATAL: ESP Lab activation started without complete backups\n' >&2
      exit 70
    fi
    if [[ $HAD_INSTALL_ROOT -eq 1 ]]; then
      rm -rf "$INSTALL_ROOT"
      mkdir -p "$(dirname -- "$INSTALL_ROOT")"
      cp -a "$BACKUP_DIR/install-root" "$INSTALL_ROOT"
    else
      rm -rf "$INSTALL_ROOT"
    fi
    if [[ $HAD_EXEC_ROOT -eq 1 ]]; then
      rm -rf "$EXEC_ROOT"
      mkdir -p "$(dirname -- "$EXEC_ROOT")"
      cp -a "$BACKUP_DIR/exec-root" "$EXEC_ROOT"
    else
      rm -rf "$EXEC_ROOT"
    fi
    if [[ $HAD_SUDOERS -eq 1 ]]; then
      install -m 0440 "$BACKUP_DIR/sudoers" "$SUDOERS_PATH"
      chown_root_if_live "$SUDOERS_PATH"
    else
      rm -f "$SUDOERS_PATH"
    fi
  fi
  rm -rf "$BACKUP_DIR"
  exit "$rc"
}
trap cleanup EXIT

if [[ $# -gt 0 ]]; then
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
fi

PAYLOAD_FILE="$(mktemp /tmp/skeleton-home-edge-esp-lab-payload.XXXXXX)"
cat > "$PAYLOAD_FILE"

validate_payload() {
  /usr/bin/python3 - "$PAYLOAD_FILE" <<'PY'
import json
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    raise SystemExit(f"invalid JSON stdin payload: {exc}")
if not isinstance(payload, dict):
    raise SystemExit("stdin payload must be a JSON object")

required = {
    "schema",
    "operation",
    "approved_git_head",
    "repo_root",
    "protected_installer_path",
    "install_root",
    "exec_root",
    "sudoers_path",
    "runner_user",
    "runner_service",
    "fake_root",
}
unknown = set(payload) - required
missing = required - set(payload)
if unknown:
    raise SystemExit(f"unknown payload field: {sorted(unknown)[0]}")
if missing:
    raise SystemExit(f"missing payload field: {sorted(missing)[0]}")
if payload["schema"] != "skeleton.home_edge.esp_lab.installer.v1":
    raise SystemExit("invalid payload schema")
if payload["operation"] != "install_home_edge_esp_lab_runtime_v1":
    raise SystemExit("invalid payload operation")
if not isinstance(payload["fake_root"], bool):
    raise SystemExit("fake_root must be a boolean")
if not re.fullmatch(r"[0-9a-f]{40}", payload["approved_git_head"]):
    raise SystemExit("approved_git_head must be a git SHA")
if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*[$]?", payload["runner_user"]):
    raise SystemExit("runner_user is unsafe")
if not re.fullmatch(r"[A-Za-z0-9_.@:-]{1,128}", payload["runner_service"]):
    raise SystemExit("runner_service is unsafe")
for key in ("repo_root", "protected_installer_path", "install_root", "exec_root", "sudoers_path"):
    value = payload[key]
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value or "\n" in value:
        raise SystemExit(f"{key} must be an absolute path")
path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
PY
}

payload_field() {
  /usr/bin/python3 - "$PAYLOAD_FILE" "$1" <<'PY'
import json
import sys
print(json.loads(open(sys.argv[1], encoding="utf-8").read())[sys.argv[2]])
PY
}

validate_payload || die "invalid stdin payload"

APPROVED_GIT_HEAD="$(payload_field approved_git_head)"
REPO_ROOT="$(payload_field repo_root)"
PROTECTED_INSTALLER_PATH="$(payload_field protected_installer_path)"
INSTALL_ROOT="$(payload_field install_root)"
EXEC_ROOT="$(payload_field exec_root)"
SUDOERS_PATH="$(payload_field sudoers_path)"
RUNNER_USER="$(payload_field runner_user)"
RUNNER_SERVICE="$(payload_field runner_service)"
FAKE_ROOT="$(payload_field fake_root)"

chown_root_if_live() {
  if [[ "$FAKE_ROOT" == "False" || "$FAKE_ROOT" == "false" ]]; then
    chown root:root "$@"
  fi
}

if [[ "$FAKE_ROOT" != "True" && "$FAKE_ROOT" != "true" && "$(id -u)" -ne 0 ]]; then
  die "installer must run as root"
fi
if [[ "$(readlink -f -- "$0")" != "$PROTECTED_INSTALLER_PATH" ]]; then
  die "root must execute only protected installed ESP Lab installer copy"
fi
if [[ -L "$PROTECTED_INSTALLER_PATH" || ! -f "$PROTECTED_INSTALLER_PATH" ]]; then
  die "protected ESP Lab installer copy is unsafe"
fi
protected_mode="$(stat -c '%a' -- "$PROTECTED_INSTALLER_PATH")"
if (( (8#$protected_mode & 8#022) != 0 )); then
  die "protected ESP Lab installer copy mode is unsafe"
fi
if [[ "$FAKE_ROOT" != "True" && "$FAKE_ROOT" != "true" ]]; then
  protected_uid="$(stat -c '%u' -- "$PROTECTED_INSTALLER_PATH")"
  protected_gid="$(stat -c '%g' -- "$PROTECTED_INSTALLER_PATH")"
  if [[ "$protected_uid" != "0" || "$protected_gid" != "0" ]]; then
    die "protected ESP Lab installer copy ownership is unsafe"
  fi
fi
if ! getent passwd "$RUNNER_USER" >/dev/null; then
  die "canonical runner user is unavailable"
fi
actual_runner_user="$(systemctl show --property=User --value "$RUNNER_SERVICE" 2>/dev/null || true)"
if [[ "$actual_runner_user" != "$RUNNER_USER" ]]; then
  die "live Runner service user does not match canonical account"
fi
if [[ -L "$REPO_ROOT" || ! -d "$REPO_ROOT/.git" ]]; then
  die "repo_root is not a Skeleton git worktree"
fi
actual_head="$(git -C "$REPO_ROOT" rev-parse HEAD)"
if [[ "$actual_head" != "$APPROVED_GIT_HEAD" ]]; then
  die "repository head does not match approved ESP Lab installer payload"
fi

reviewed_blob_sha() {
  git -C "$REPO_ROOT" hash-object --no-filters --stdin < "$1"
}

validate_source_file() {
  local rel="$1" max_bytes="$2" expected_blob="$3" path size mode actual_blob
  path="$REPO_ROOT/$rel"
  if [[ -L "$path" || ! -f "$path" || ! -r "$path" ]]; then
    die "reviewed ESP Lab source is not a readable regular file"
  fi
  size="$(stat -c '%s' -- "$path")"
  mode="$(stat -c '%a' -- "$path")"
  if (( size <= 0 || size > max_bytes )); then
    die "reviewed ESP Lab source size is unsafe"
  fi
  if (( (8#$mode & 8#022) != 0 )); then
    die "reviewed ESP Lab source is group/world writable"
  fi
  actual_blob="$(reviewed_blob_sha "$path")"
  if [[ "$actual_blob" != "$expected_blob" ]]; then
    die "reviewed ESP Lab source bytes do not match approved blob"
  fi
}

validate_source_file "scripts/home_edge_esp_lab.py" $((16 * 1024)) "$APPROVED_SCRIPT_BLOB_SHA"
validate_source_file "core/home_edge/esp_lab.py" $((128 * 1024)) "$APPROVED_CORE_BLOB_SHA"
validate_source_file "schemas/home_edge_esp_lab_job.schema.json" $((16 * 1024)) "$APPROVED_JOB_SCHEMA_BLOB_SHA"
validate_source_file "schemas/home_edge_esp_lab_observation.schema.json" $((16 * 1024)) "$APPROVED_OBSERVATION_SCHEMA_BLOB_SHA"
validate_source_file "schemas/home_edge_esp_lab_receipt.schema.json" $((16 * 1024)) "$APPROVED_RECEIPT_SCHEMA_BLOB_SHA"

for path in "$INSTALL_ROOT" "$EXEC_ROOT"; do
  if [[ -L "$path" || ( -e "$path" && ! -d "$path" ) ]]; then
    die "existing ESP Lab runtime path is unsafe"
  fi
done
if [[ -L "$SUDOERS_PATH" || ( -e "$SUDOERS_PATH" && ! -f "$SUDOERS_PATH" ) ]]; then
  die "existing ESP Lab sudoers entry is unsafe"
fi
[[ -e "$INSTALL_ROOT" ]] && HAD_INSTALL_ROOT=1
[[ -e "$EXEC_ROOT" ]] && HAD_EXEC_ROOT=1
[[ -e "$SUDOERS_PATH" ]] && HAD_SUDOERS=1

BACKUP_DIR="$(mktemp -d /tmp/skeleton-home-edge-esp-lab.XXXXXX)"
STAGING_PARENT="$(mktemp -d /tmp/skeleton-home-edge-esp-lab-stage.XXXXXX)"
if [[ $HAD_INSTALL_ROOT -eq 1 ]]; then cp -a "$INSTALL_ROOT" "$BACKUP_DIR/install-root"; fi
if [[ $HAD_EXEC_ROOT -eq 1 ]]; then cp -a "$EXEC_ROOT" "$BACKUP_DIR/exec-root"; fi
if [[ $HAD_SUDOERS -eq 1 ]]; then cp -a "$SUDOERS_PATH" "$BACKUP_DIR/sudoers"; fi
BACKUPS_READY=1

mkdir -p \
  "$STAGING_PARENT/install/core/home_edge" \
  "$STAGING_PARENT/install/scripts" \
  "$STAGING_PARENT/install/schemas" \
  "$STAGING_PARENT/exec"

copy_stable_source() {
  local rel="$1" destination="$2" mode="$3" expected_blob="$4" source before after staged_blob
  source="$REPO_ROOT/$rel"
  before="$(stat -c '%d:%i:%s:%Y:%Z:%a:%u:%g' -- "$source")"
  cp --no-dereference -- "$source" "$destination"
  if [[ -L "$destination" || ! -f "$destination" ]]; then
    die "inert ESP Lab copy did not produce a regular file"
  fi
  chmod "$mode" "$destination"
  chown_root_if_live "$destination"
  after="$(stat -c '%d:%i:%s:%Y:%Z:%a:%u:%g' -- "$source")"
  staged_blob="$(reviewed_blob_sha "$destination")"
  if [[ "$before" != "$after" || "$staged_blob" != "$expected_blob" ]]; then
    die "reviewed ESP Lab source changed during inert copy"
  fi
}

copy_stable_source "scripts/home_edge_esp_lab.py" "$STAGING_PARENT/install/scripts/home_edge_esp_lab.py" 0555 "$APPROVED_SCRIPT_BLOB_SHA"
copy_stable_source "core/home_edge/esp_lab.py" "$STAGING_PARENT/install/core/home_edge/esp_lab.py" 0444 "$APPROVED_CORE_BLOB_SHA"
copy_stable_source "schemas/home_edge_esp_lab_job.schema.json" "$STAGING_PARENT/install/schemas/home_edge_esp_lab_job.schema.json" 0444 "$APPROVED_JOB_SCHEMA_BLOB_SHA"
copy_stable_source "schemas/home_edge_esp_lab_observation.schema.json" "$STAGING_PARENT/install/schemas/home_edge_esp_lab_observation.schema.json" 0444 "$APPROVED_OBSERVATION_SCHEMA_BLOB_SHA"
copy_stable_source "schemas/home_edge_esp_lab_receipt.schema.json" "$STAGING_PARENT/install/schemas/home_edge_esp_lab_receipt.schema.json" 0444 "$APPROVED_RECEIPT_SCHEMA_BLOB_SHA"

printf '%s\n' '"""Installed Skeleton core package for ESP Lab."""' > "$STAGING_PARENT/install/core/__init__.py"
printf '%s\n' '"""Installed Home Edge package for ESP Lab."""' > "$STAGING_PARENT/install/core/home_edge/__init__.py"
chmod 0444 "$STAGING_PARENT/install/core/__init__.py" "$STAGING_PARENT/install/core/home_edge/__init__.py"
chown_root_if_live "$STAGING_PARENT/install/core/__init__.py" "$STAGING_PARENT/install/core/home_edge/__init__.py"

cat > "$STAGING_PARENT/exec/home_edge_esp_lab" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
exec env -i \\
  PATH="/usr/sbin:/usr/bin:/sbin:/bin" \\
  LANG="\${LANG:-C.UTF-8}" \\
  LC_ALL="\${LC_ALL:-}" \\
  PYTHONPATH="$INSTALL_ROOT" \\
  /usr/bin/python3 "$INSTALL_ROOT/scripts/home_edge_esp_lab.py" "\$@"
EOF
chmod 0555 "$STAGING_PARENT/exec/home_edge_esp_lab"
chown_root_if_live "$STAGING_PARENT/exec/home_edge_esp_lab"

install -d -m 0755 "$(dirname -- "$INSTALL_ROOT")" "$(dirname -- "$EXEC_ROOT")" "$(dirname -- "$SUDOERS_PATH")"
chown_root_if_live "$(dirname -- "$INSTALL_ROOT")" "$(dirname -- "$EXEC_ROOT")" "$(dirname -- "$SUDOERS_PATH")"
rm -rf "$INSTALL_ROOT.new" "$EXEC_ROOT.new"
cp -a "$STAGING_PARENT/install" "$INSTALL_ROOT.new"
cp -a "$STAGING_PARENT/exec" "$EXEC_ROOT.new"
chmod 0555 "$INSTALL_ROOT.new" "$EXEC_ROOT.new"
find "$INSTALL_ROOT.new" "$EXEC_ROOT.new" -type d -exec chmod 0555 {} +
find "$INSTALL_ROOT.new" "$EXEC_ROOT.new" -type f -exec sh -c 'case "$1" in */scripts/home_edge_esp_lab.py|*/home_edge_esp_lab) chmod 0555 "$1";; *) chmod 0444 "$1";; esac' sh {} \;
chown_root_if_live -R "$INSTALL_ROOT.new" "$EXEC_ROOT.new"

for installed in \
  "$INSTALL_ROOT.new/scripts/home_edge_esp_lab.py" \
  "$INSTALL_ROOT.new/core/home_edge/esp_lab.py" \
  "$INSTALL_ROOT.new/schemas/home_edge_esp_lab_job.schema.json" \
  "$INSTALL_ROOT.new/schemas/home_edge_esp_lab_observation.schema.json" \
  "$INSTALL_ROOT.new/schemas/home_edge_esp_lab_receipt.schema.json" \
  "$EXEC_ROOT.new/home_edge_esp_lab"; do
  if [[ -L "$installed" || ! -f "$installed" ]]; then
    die "immutable ESP Lab staging is unsafe"
  fi
  mode="$(stat -c '%a' -- "$installed")"
  if (( (8#$mode & 8#022) != 0 )); then
    die "immutable ESP Lab staging is writable by group/world"
  fi
done
if [[ "$(reviewed_blob_sha "$INSTALL_ROOT.new/scripts/home_edge_esp_lab.py")" != "$APPROVED_SCRIPT_BLOB_SHA" \
   || "$(reviewed_blob_sha "$INSTALL_ROOT.new/core/home_edge/esp_lab.py")" != "$APPROVED_CORE_BLOB_SHA" \
   || "$(reviewed_blob_sha "$INSTALL_ROOT.new/schemas/home_edge_esp_lab_job.schema.json")" != "$APPROVED_JOB_SCHEMA_BLOB_SHA" \
   || "$(reviewed_blob_sha "$INSTALL_ROOT.new/schemas/home_edge_esp_lab_observation.schema.json")" != "$APPROVED_OBSERVATION_SCHEMA_BLOB_SHA" \
   || "$(reviewed_blob_sha "$INSTALL_ROOT.new/schemas/home_edge_esp_lab_receipt.schema.json")" != "$APPROVED_RECEIPT_SCHEMA_BLOB_SHA" ]]; then
  die "immutable ESP Lab staging bytes changed"
fi
if [[ $BACKUPS_READY -ne 1 ]]; then
  die "ESP Lab activation backups are incomplete"
fi

ACTIVATION_STARTED=1
rm -f "$SUDOERS_PATH"
if [[ $HAD_INSTALL_ROOT -eq 1 ]]; then rm -rf "$INSTALL_ROOT"; fi
if [[ $HAD_EXEC_ROOT -eq 1 ]]; then rm -rf "$EXEC_ROOT"; fi
mv -T "$INSTALL_ROOT.new" "$INSTALL_ROOT"
mv -T "$EXEC_ROOT.new" "$EXEC_ROOT"

cat > "$BACKUP_DIR/sudoers.new" <<EOF
$RUNNER_USER ALL=(root) NOPASSWD: $EXEC_ROOT/home_edge_esp_lab *
EOF
chmod 0440 "$BACKUP_DIR/sudoers.new"
visudo -cf "$BACKUP_DIR/sudoers.new" >/dev/null
install -m 0440 "$BACKUP_DIR/sudoers.new" "$SUDOERS_PATH"
chown_root_if_live "$SUDOERS_PATH"

COMMITTED=1
rm -rf "$STAGING_PARENT" "$BACKUP_DIR"
trap - EXIT
rm -f "$PAYLOAD_FILE"

printf 'DONE: Home Edge ESP Lab immutable runtime installed\n'
printf 'approved_git_head=%s\n' "$APPROVED_GIT_HEAD"
printf 'runtime=%s\n' "$INSTALL_ROOT"
printf 'executable=%s\n' "$EXEC_ROOT/home_edge_esp_lab"
printf 'sudoers=%s\n' "$SUDOERS_PATH"
