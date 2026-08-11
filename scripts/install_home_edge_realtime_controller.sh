#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="/home/agent/agent-dev/repos/Skeleton"
INSTALL_BIN="/usr/local/bin"
INSTALL_LIB="/usr/local/lib/skeleton-home-edge-controller"
CONFIG_DIR="/etc/skeleton/mcp"
BACKUP_DIR=""
COMMITTED=0
RUNNER_SERVICE_USER="agent"

usage() {
  cat <<'EOF'
Usage: sudo scripts/install_home_edge_realtime_controller.sh [--repo-root PATH]

Installs the stdio MCP launcher, public-safe health probe, and protected
snapshot signer on the trusted controller. It reuses the existing Home Edge
profile, SSH identity and HMAC secret. It does not create a service, key or
secret.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root)
      REPO_ROOT="${2:?missing value for --repo-root}"
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

required=(
  "$REPO_ROOT/scripts/home_edge_exec_mcp.py"
  "$REPO_ROOT/scripts/home_edge_exec_mcp_launcher.sh"
  "$REPO_ROOT/scripts/home_edge_exec_mcp_probe.py"
  "$REPO_ROOT/scripts/home_edge_media_source_snapshot_signer.py"
  "$REPO_ROOT/core/home_edge/executor.py"
  "$REPO_ROOT/core/home_edge/executor_gateway.py"
  "$REPO_ROOT/core/home_edge/media_source_snapshot.py"
  "$REPO_ROOT/config/mcp/skeleton-home-edge-exec.json"
  "/etc/skeleton/home-edge-01.env"
  "/etc/skeleton/home-edge-executor-controller.env"
)
for path in "${required[@]}"; do
  if [[ ! -r "$path" ]]; then
    printf 'BLOCKED: required controller input is unavailable\n' >&2
    exit 2
  fi
done
if ! id -u "$RUNNER_SERVICE_USER" >/dev/null 2>&1; then
  printf 'BLOCKED: canonical Runner service identity is unavailable\n' >&2
  exit 2
fi

BACKUP_DIR="$(mktemp -d /tmp/skeleton-home-edge-controller.XXXXXX)"
rollback() {
  local rc=$?
  if [[ $COMMITTED -eq 1 ]]; then
    rm -rf "$BACKUP_DIR"
    return
  fi
  for name in skeleton-home-edge-exec-mcp skeleton-home-edge-exec-probe skeleton-home-edge-media-source-snapshot-signer; do
    if [[ -e "$BACKUP_DIR/$name" ]]; then
      cp -a "$BACKUP_DIR/$name" "$INSTALL_BIN/$name"
    else
      rm -f "$INSTALL_BIN/$name"
    fi
  done
  if [[ -e "$BACKUP_DIR/skeleton-home-edge-exec.json" ]]; then
    install -o root -g root -m 0644 "$BACKUP_DIR/skeleton-home-edge-exec.json" "$CONFIG_DIR/skeleton-home-edge-exec.json"
  else
    rm -f "$CONFIG_DIR/skeleton-home-edge-exec.json"
  fi
  if [[ -e "$BACKUP_DIR/controller-lib" ]]; then
    rm -rf "$INSTALL_LIB"
    cp -a "$BACKUP_DIR/controller-lib" "$INSTALL_LIB"
  else
    rm -rf "$INSTALL_LIB"
  fi
  rm -rf "$BACKUP_DIR"
  exit "$rc"
}
trap rollback EXIT

mkdir -p "$CONFIG_DIR"
for name in skeleton-home-edge-exec-mcp skeleton-home-edge-exec-probe skeleton-home-edge-media-source-snapshot-signer; do
  if [[ -e "$INSTALL_BIN/$name" ]]; then
    cp -a "$INSTALL_BIN/$name" "$BACKUP_DIR/$name"
  fi
done
if [[ -e "$INSTALL_LIB" ]]; then
  cp -a "$INSTALL_LIB" "$BACKUP_DIR/controller-lib"
fi
if [[ -e "$CONFIG_DIR/skeleton-home-edge-exec.json" ]]; then
  cp -a "$CONFIG_DIR/skeleton-home-edge-exec.json" "$BACKUP_DIR/skeleton-home-edge-exec.json"
fi

install -o root -g root -m 0755 \
  "$REPO_ROOT/scripts/home_edge_exec_mcp_launcher.sh" \
  "$INSTALL_BIN/skeleton-home-edge-exec-mcp"
install -o root -g root -m 0755 \
  "$REPO_ROOT/scripts/home_edge_exec_mcp_probe.py" \
  "$INSTALL_BIN/skeleton-home-edge-exec-probe"
install -o root -g root -m 0644 \
  "$REPO_ROOT/config/mcp/skeleton-home-edge-exec.json" \
  "$CONFIG_DIR/skeleton-home-edge-exec.json"
rm -rf "$INSTALL_LIB"
mkdir -p "$INSTALL_LIB/core/home_edge" "$INSTALL_LIB/scripts"
install -o root -g root -m 0644 "$REPO_ROOT/core/__init__.py" "$INSTALL_LIB/core/__init__.py"
install -o root -g root -m 0644 "$REPO_ROOT/core/home_edge/executor.py" "$INSTALL_LIB/core/home_edge/executor.py"
install -o root -g root -m 0644 "$REPO_ROOT/core/home_edge/executor_gateway.py" "$INSTALL_LIB/core/home_edge/executor_gateway.py"
install -o root -g root -m 0644 "$REPO_ROOT/core/home_edge/media_source_snapshot.py" "$INSTALL_LIB/core/home_edge/media_source_snapshot.py"
install -o root -g root -m 0755 "$REPO_ROOT/scripts/home_edge_media_source_snapshot_signer.py" "$INSTALL_LIB/scripts/home_edge_media_source_snapshot_signer.py"
cat > "$INSTALL_BIN/skeleton-home-edge-media-source-snapshot-signer" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "\${1:-}" != "--sign" || "\$#" -ne 1 ]]; then
  printf 'BLOCKED: unsupported signer invocation\n' >&2
  exit 2
fi
exec env -i \\
  PATH="/usr/sbin:/usr/bin:/sbin:/bin" \\
  LANG="\${LANG:-C.UTF-8}" \\
  PYTHONPATH="$INSTALL_LIB" \\
  /usr/bin/python3 "$INSTALL_LIB/scripts/home_edge_media_source_snapshot_signer.py" --sign
EOF
chown root:"$RUNNER_SERVICE_USER" "$INSTALL_BIN/skeleton-home-edge-media-source-snapshot-signer"
chmod 0750 "$INSTALL_BIN/skeleton-home-edge-media-source-snapshot-signer"
python3 -m py_compile \
  "$INSTALL_LIB/core/home_edge/executor.py" \
  "$INSTALL_LIB/core/home_edge/executor_gateway.py" \
  "$INSTALL_LIB/core/home_edge/media_source_snapshot.py" \
  "$INSTALL_LIB/scripts/home_edge_media_source_snapshot_signer.py"

SKELETON_HOME_EDGE_REPO_ROOT="$REPO_ROOT" \
  "$INSTALL_BIN/skeleton-home-edge-exec-probe" --skip-call
SKELETON_HOME_EDGE_REPO_ROOT="$REPO_ROOT" \
  "$INSTALL_BIN/skeleton-home-edge-exec-probe"

COMMITTED=1
rm -rf "$BACKUP_DIR"
trap - EXIT

printf 'DONE: realtime Home Edge stdio MCP controller installed and verified\n'
printf 'registration_config=%s\n' "$CONFIG_DIR/skeleton-home-edge-exec.json"
printf 'next=register this stdio MCP server in the actual Jeeves/Skeleton tool host\n'
