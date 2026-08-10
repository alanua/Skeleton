#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="/home/agent/agent-dev/repos/Skeleton"
RUNNER_USER="${SUDO_USER:-agent}"
INSTALL_ROOT="/usr/local/lib/skeleton/home-edge/media-source-snapshot"
EXEC_ROOT="/usr/local/libexec/skeleton/home-edge/media-source-snapshot"
SUDOERS_PATH="/etc/sudoers.d/skeleton-home-edge-media-source-snapshot-signer"
PAYLOAD_REL="scripts/home_edge_media_source_snapshot_signer_payload.py"
WRAPPER_REL="scripts/home_edge_media_source_snapshot_signer"
CONTRACT_REL="core/home_edge/media_source_snapshot.py"
COMMITTED=0
BACKUP_DIR=""
STAGING_PARENT=""

usage() {
  cat <<'EOF'
Usage: sudo scripts/install_home_edge_media_source_snapshot_signer.sh [--repo-root PATH] [--runner-user USER]

Copies reviewed signer files as inert data into a root-owned immutable runtime.
No Python, module import, eval, compile, repository executable, or repository
working-directory execution is allowed in the privileged installer.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root)
      REPO_ROOT="${2:?missing value for --repo-root}"
      shift 2
      ;;
    --runner-user)
      RUNNER_USER="${2:?missing value for --runner-user}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  printf 'BLOCKED: installer must run as root\n' >&2
  exit 2
fi
if ! getent passwd "$RUNNER_USER" >/dev/null; then
  printf 'BLOCKED: runner user is unavailable\n' >&2
  exit 2
fi

PAYLOAD_SRC="$REPO_ROOT/$PAYLOAD_REL"
WRAPPER_SRC="$REPO_ROOT/$WRAPPER_REL"
CONTRACT_SRC="$REPO_ROOT/$CONTRACT_REL"

validate_source_file() {
  local path="$1" max_bytes="$2" size mode
  if [[ -L "$path" || ! -f "$path" || ! -r "$path" ]]; then
    printf 'BLOCKED: reviewed signer source is not a readable regular file\n' >&2
    exit 2
  fi
  size="$(stat -c '%s' -- "$path")"
  mode="$(stat -c '%a' -- "$path")"
  if (( size <= 0 || size > max_bytes )); then
    printf 'BLOCKED: reviewed signer source size is unsafe\n' >&2
    exit 2
  fi
  if (( (8#$mode & 8#022) != 0 )); then
    printf 'BLOCKED: reviewed signer source is group/world writable\n' >&2
    exit 2
  fi
}

validate_source_file "$PAYLOAD_SRC" $((128 * 1024))
validate_source_file "$WRAPPER_SRC" $((16 * 1024))
validate_source_file "$CONTRACT_SRC" $((256 * 1024))
for path in /etc/skeleton /etc/skeleton/home-edge-01.env /etc/skeleton/home-edge-executor-controller.env; do
  if [[ -L "$path" || ! -e "$path" ]]; then
    printf 'BLOCKED: controller boundary metadata is unavailable\n' >&2
    exit 2
  fi
done

BACKUP_DIR="$(mktemp -d /tmp/skeleton-home-edge-snapshot-signer.XXXXXX)"
STAGING_PARENT="$(mktemp -d /tmp/skeleton-home-edge-snapshot-signer-stage.XXXXXX)"
rollback() {
  local rc=$?
  trap - EXIT
  rm -rf "$STAGING_PARENT"
  if [[ $COMMITTED -eq 0 ]]; then
    if [[ -e "$BACKUP_DIR/install-root" ]]; then
      rm -rf "$INSTALL_ROOT"
      mkdir -p "$(dirname "$INSTALL_ROOT")"
      mv "$BACKUP_DIR/install-root" "$INSTALL_ROOT"
    fi
    if [[ -e "$BACKUP_DIR/exec-root" ]]; then
      rm -rf "$EXEC_ROOT"
      mkdir -p "$(dirname "$EXEC_ROOT")"
      mv "$BACKUP_DIR/exec-root" "$EXEC_ROOT"
    fi
    if [[ -e "$BACKUP_DIR/sudoers" ]]; then
      install -o root -g root -m 0440 "$BACKUP_DIR/sudoers" "$SUDOERS_PATH"
    else
      rm -f "$SUDOERS_PATH"
    fi
  fi
  rm -rf "$BACKUP_DIR"
  exit "$rc"
}
trap rollback EXIT

[[ ! -e "$INSTALL_ROOT" ]] || cp -a "$INSTALL_ROOT" "$BACKUP_DIR/install-root"
[[ ! -e "$EXEC_ROOT" ]] || cp -a "$EXEC_ROOT" "$BACKUP_DIR/exec-root"
[[ ! -e "$SUDOERS_PATH" ]] || cp -a "$SUDOERS_PATH" "$BACKUP_DIR/sudoers"
mkdir -p "$STAGING_PARENT/install" "$STAGING_PARENT/exec"

copy_stable_source() {
  local source="$1" destination="$2" mode="$3" before after source_hash destination_hash
  before="$(stat -Lc '%d:%i:%s:%Y' -- "$source")"
  install -o root -g root -m "$mode" -- "$source" "$destination"
  after="$(stat -Lc '%d:%i:%s:%Y' -- "$source")"
  source_hash="$(sha256sum -- "$source" | awk '{print $1}')"
  destination_hash="$(sha256sum -- "$destination" | awk '{print $1}')"
  if [[ "$before" != "$after" || "$source_hash" != "$destination_hash" ]]; then
    printf 'BLOCKED: reviewed signer source changed during inert copy\n' >&2
    exit 2
  fi
}

copy_stable_source "$PAYLOAD_SRC" "$STAGING_PARENT/install/signer_payload.py" 0555
copy_stable_source "$CONTRACT_SRC" "$STAGING_PARENT/install/contract_source.py" 0444
copy_stable_source "$WRAPPER_SRC" "$STAGING_PARENT/exec/signer" 0555

install -d -o root -g root -m 0755 "$(dirname "$INSTALL_ROOT")" "$(dirname "$EXEC_ROOT")"
rm -rf "$INSTALL_ROOT.new" "$EXEC_ROOT.new"
install -d -o root -g root -m 0755 "$INSTALL_ROOT.new" "$EXEC_ROOT.new"
install -o root -g root -m 0555 "$STAGING_PARENT/install/signer_payload.py" "$INSTALL_ROOT.new/signer_payload.py"
install -o root -g root -m 0444 "$STAGING_PARENT/install/contract_source.py" "$INSTALL_ROOT.new/contract_source.py"
install -o root -g root -m 0555 "$STAGING_PARENT/exec/signer" "$EXEC_ROOT.new/signer"

for installed in "$INSTALL_ROOT.new/signer_payload.py" "$INSTALL_ROOT.new/contract_source.py" "$EXEC_ROOT.new/signer"; do
  if [[ -L "$installed" || ! -f "$installed" ]]; then
    printf 'BLOCKED: immutable signer staging is unsafe\n' >&2
    exit 2
  fi
  mode="$(stat -c '%a' -- "$installed")"
  if (( (8#$mode & 8#022) != 0 )); then
    printf 'BLOCKED: immutable signer staging is writable by group/world\n' >&2
    exit 2
  fi
done

mv -T "$INSTALL_ROOT.new" "$INSTALL_ROOT"
mv -T "$EXEC_ROOT.new" "$EXEC_ROOT"

cat > "$BACKUP_DIR/sudoers.new" <<EOF
$RUNNER_USER ALL=(root) NOPASSWD: $EXEC_ROOT/signer ""
EOF
chmod 0440 "$BACKUP_DIR/sudoers.new"
visudo -cf "$BACKUP_DIR/sudoers.new" >/dev/null
install -o root -g root -m 0440 "$BACKUP_DIR/sudoers.new" "$SUDOERS_PATH"

COMMITTED=1
rm -rf "$STAGING_PARENT" "$BACKUP_DIR"
trap - EXIT
printf 'DONE: Home Edge media source snapshot signer installed\n'
printf 'signer=%s\n' "$EXEC_ROOT/signer"
printf 'payload=%s\n' "$INSTALL_ROOT/signer_payload.py"
printf 'contract=%s\n' "$INSTALL_ROOT/contract_source.py"
printf 'sudoers=%s\n' "$SUDOERS_PATH"
