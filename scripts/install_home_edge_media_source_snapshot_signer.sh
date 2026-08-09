#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="/home/agent/agent-dev/repos/Skeleton"
RUNNER_USER="${SUDO_USER:-agent}"
INSTALL_ROOT="/usr/local/lib/skeleton/home-edge/media-source-snapshot"
EXEC_ROOT="/usr/local/libexec/skeleton/home-edge/media-source-snapshot"
SUDOERS_PATH="/etc/sudoers.d/skeleton-home-edge-media-source-snapshot-signer"
COMMITTED=0
BACKUP_DIR=""

usage() {
  cat <<'EOF'
Usage: sudo scripts/install_home_edge_media_source_snapshot_signer.sh [--repo-root PATH] [--runner-user USER]

Installs the immutable Home Edge media source snapshot signer payload outside
the mutable Runner checkout. The installed signer signs only the exact
approved snapshot executor request and never executes transport.
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

for path in \
  "$REPO_ROOT/core/home_edge/media_source_snapshot.py" \
  "$REPO_ROOT/core/home_edge/executor.py" \
  "/etc/skeleton/home-edge-01.env" \
  "/etc/skeleton/home-edge-executor-controller.env"; do
  if [[ ! -r "$path" ]]; then
    printf 'BLOCKED: required signer install input is unavailable\n' >&2
    exit 2
  fi
done

BACKUP_DIR="$(mktemp -d /tmp/skeleton-home-edge-snapshot-signer.XXXXXX)"
rollback() {
  local rc=$?
  if [[ $COMMITTED -eq 1 ]]; then
    rm -rf "$BACKUP_DIR"
    return
  fi
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
  rm -rf "$BACKUP_DIR"
  exit "$rc"
}
trap rollback EXIT

[[ ! -e "$INSTALL_ROOT" ]] || cp -a "$INSTALL_ROOT" "$BACKUP_DIR/install-root"
[[ ! -e "$EXEC_ROOT" ]] || cp -a "$EXEC_ROOT" "$BACKUP_DIR/exec-root"
[[ ! -e "$SUDOERS_PATH" ]] || cp -a "$SUDOERS_PATH" "$BACKUP_DIR/sudoers"

STAGING_PARENT="$(mktemp -d /tmp/skeleton-home-edge-snapshot-signer-stage.XXXXXX)"
trap 'rm -rf "$STAGING_PARENT"; rollback' EXIT
mkdir -p "$STAGING_PARENT/install" "$STAGING_PARENT/exec"

PYTHONPATH="$REPO_ROOT" /usr/bin/python3 - <<'PY' > "$STAGING_PARENT/install/signer_payload.py"
from core.home_edge.media_source_snapshot import installed_signer_payload_source

print(installed_signer_payload_source(), end="")
PY

PYTHONPATH="$REPO_ROOT" /usr/bin/python3 - <<'PY' > "$STAGING_PARENT/exec/signer"
from core.home_edge.media_source_snapshot import installed_signer_wrapper_source

print(installed_signer_wrapper_source(), end="")
PY

if grep -R -E '/home/agent/|PYTHONPATH|core\.home_edge|agent-dev|worktrees|repos/Skeleton' "$STAGING_PARENT/install" "$STAGING_PARENT/exec"; then
  printf 'BLOCKED: installed signer payload contains mutable repository coupling\n' >&2
  exit 2
fi

/usr/bin/python3 -m py_compile "$STAGING_PARENT/install/signer_payload.py"

install -d -o root -g root -m 0755 "$(dirname "$INSTALL_ROOT")" "$(dirname "$EXEC_ROOT")"
rm -rf "$INSTALL_ROOT.new" "$EXEC_ROOT.new"
install -d -o root -g root -m 0755 "$INSTALL_ROOT.new" "$EXEC_ROOT.new"
install -o root -g root -m 0555 "$STAGING_PARENT/install/signer_payload.py" "$INSTALL_ROOT.new/signer_payload.py"
install -o root -g root -m 0555 "$STAGING_PARENT/exec/signer" "$EXEC_ROOT.new/signer"
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
printf 'sudoers=%s\n' "$SUDOERS_PATH"
