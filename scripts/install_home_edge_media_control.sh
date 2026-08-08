#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="/home/agent/agent-dev/repos/Skeleton"
INSTALL_BIN="/usr/local/bin"
CONFIG_DIR="/etc/skeleton/mcp"
BACKUP_DIR=""
COMMITTED=0

usage() {
  cat <<'EOF'
Usage: sudo scripts/install_home_edge_media_control.sh [--repo-root PATH]

Installs the bounded Home Edge media MCP launcher, public-safe probe, and
registration fragment. It reuses the existing private Home Edge profile,
SSH identity, and signing secret. It does not expose a network listener.
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
  "$REPO_ROOT/scripts/home_edge_control_mcp.py"
  "$REPO_ROOT/scripts/home_edge_control_mcp_launcher.sh"
  "$REPO_ROOT/scripts/home_edge_control_mcp_probe.py"
  "$REPO_ROOT/config/mcp/skeleton-home-media-control.json"
  "/etc/skeleton/home-edge-01.env"
  "/etc/skeleton/home-edge-executor-controller.env"
)
for path in "${required[@]}"; do
  if [[ ! -r "$path" ]]; then
    printf 'BLOCKED: required media-control input is unavailable\n' >&2
    exit 2
  fi
done

BACKUP_DIR="$(mktemp -d /tmp/skeleton-home-media-control.XXXXXX)"
rollback() {
  local rc=$?
  if [[ $COMMITTED -eq 1 ]]; then
    rm -rf "$BACKUP_DIR"
    return
  fi
  for name in skeleton-home-media-control-mcp skeleton-home-media-control-probe; do
    if [[ -e "$BACKUP_DIR/$name" ]]; then
      install -o root -g root -m 0755 "$BACKUP_DIR/$name" "$INSTALL_BIN/$name"
    else
      rm -f "$INSTALL_BIN/$name"
    fi
  done
  if [[ -e "$BACKUP_DIR/skeleton-home-media-control.json" ]]; then
    install -o root -g root -m 0644 \
      "$BACKUP_DIR/skeleton-home-media-control.json" \
      "$CONFIG_DIR/skeleton-home-media-control.json"
  else
    rm -f "$CONFIG_DIR/skeleton-home-media-control.json"
  fi
  rm -rf "$BACKUP_DIR"
  exit "$rc"
}
trap rollback EXIT

mkdir -p "$CONFIG_DIR"
for name in skeleton-home-media-control-mcp skeleton-home-media-control-probe; do
  if [[ -e "$INSTALL_BIN/$name" ]]; then
    cp -a "$INSTALL_BIN/$name" "$BACKUP_DIR/$name"
  fi
done
if [[ -e "$CONFIG_DIR/skeleton-home-media-control.json" ]]; then
  cp -a \
    "$CONFIG_DIR/skeleton-home-media-control.json" \
    "$BACKUP_DIR/skeleton-home-media-control.json"
fi

install -o root -g root -m 0755 \
  "$REPO_ROOT/scripts/home_edge_control_mcp_launcher.sh" \
  "$INSTALL_BIN/skeleton-home-media-control-mcp"
install -o root -g root -m 0755 \
  "$REPO_ROOT/scripts/home_edge_control_mcp_probe.py" \
  "$INSTALL_BIN/skeleton-home-media-control-probe"
install -o root -g root -m 0644 \
  "$REPO_ROOT/config/mcp/skeleton-home-media-control.json" \
  "$CONFIG_DIR/skeleton-home-media-control.json"

SKELETON_HOME_EDGE_REPO_ROOT="$REPO_ROOT" \
  "$INSTALL_BIN/skeleton-home-media-control-probe" --skip-call
SKELETON_HOME_EDGE_REPO_ROOT="$REPO_ROOT" \
  "$INSTALL_BIN/skeleton-home-media-control-probe"

COMMITTED=1
rm -rf "$BACKUP_DIR"
trap - EXIT

printf 'DONE: bounded Home Edge media MCP installed and verified\n'
printf 'registration_config=%s\n' "$CONFIG_DIR/skeleton-home-media-control.json"
