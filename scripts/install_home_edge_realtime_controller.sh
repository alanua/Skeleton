#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
INSTALL_BIN="/usr/local/bin"
CONFIG_DIR="/etc/skeleton/mcp"
BACKUP_DIR=""
COMMITTED=0

usage() {
  cat <<'EOF'
Usage: sudo scripts/install_home_edge_realtime_controller.sh [--repo-root PATH]

Installs the stdio MCP launcher and public-safe health probe on the trusted
controller. It reuses the existing Home Edge profile, SSH identity and HMAC
secret. It does not create a service, key or secret.
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

BACKUP_DIR="$(mktemp -d /tmp/skeleton-home-edge-controller.XXXXXX)"
rollback() {
  local rc=$?
  if [[ $COMMITTED -eq 1 ]]; then
    rm -rf "$BACKUP_DIR"
    return
  fi
  for name in skeleton-home-edge-exec-mcp skeleton-home-edge-exec-probe; do
    if [[ -e "$BACKUP_DIR/$name" ]]; then
      install -o root -g root -m 0755 "$BACKUP_DIR/$name" "$INSTALL_BIN/$name"
    else
      rm -f "$INSTALL_BIN/$name"
    fi
  done
  if [[ -e "$BACKUP_DIR/skeleton-home-edge-exec.json" ]]; then
    install -o root -g root -m 0644 "$BACKUP_DIR/skeleton-home-edge-exec.json" "$CONFIG_DIR/skeleton-home-edge-exec.json"
  else
    rm -f "$CONFIG_DIR/skeleton-home-edge-exec.json"
  fi
  rm -rf "$BACKUP_DIR"
  exit "$rc"
}
trap rollback EXIT

mkdir -p "$CONFIG_DIR"
for name in skeleton-home-edge-exec-mcp skeleton-home-edge-exec-probe; do
  if [[ -e "$INSTALL_BIN/$name" ]]; then
    cp -a "$INSTALL_BIN/$name" "$BACKUP_DIR/$name"
  fi
done
if [[ -e "$CONFIG_DIR/skeleton-home-edge-exec.json" ]]; then
  cp -a "$CONFIG_DIR/skeleton-home-edge-exec.json" "$BACKUP_DIR/skeleton-home-edge-exec.json"
fi

# Install a self-contained launcher that preserves the exact repository root
# selected by this installer. This avoids stale controller-global defaults.
{
  printf '#!/usr/bin/env bash\nset -Eeuo pipefail\n'
  printf 'export SKELETON_HOME_EDGE_REPO_ROOT=%q\n' "$REPO_ROOT"
  printf 'exec %q\n' "$REPO_ROOT/scripts/home_edge_exec_mcp_launcher.sh"
} > "$BACKUP_DIR/installed-launcher"
install -o root -g root -m 0755 \
  "$BACKUP_DIR/installed-launcher" \
  "$INSTALL_BIN/skeleton-home-edge-exec-mcp"
install -o root -g root -m 0755 \
  "$REPO_ROOT/scripts/home_edge_exec_mcp_probe.py" \
  "$INSTALL_BIN/skeleton-home-edge-exec-probe"
install -o root -g root -m 0644 \
  "$REPO_ROOT/config/mcp/skeleton-home-edge-exec.json" \
  "$CONFIG_DIR/skeleton-home-edge-exec.json"

"$INSTALL_BIN/skeleton-home-edge-exec-probe" --skip-call
"$INSTALL_BIN/skeleton-home-edge-exec-probe"

COMMITTED=1
rm -rf "$BACKUP_DIR"
trap - EXIT

printf 'DONE: realtime Home Edge stdio MCP controller installed and verified\n'
printf 'registration_config=%s\n' "$CONFIG_DIR/skeleton-home-edge-exec.json"
printf 'next=register this stdio MCP server in the actual Jeeves/Skeleton tool host\n'
