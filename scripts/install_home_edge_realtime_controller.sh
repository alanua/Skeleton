#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="/home/agent/agent-dev/repos/Skeleton"
INSTALL_BIN="/usr/local/bin"
INSTALL_LIB="/usr/local/lib/skeleton-home-edge-controller"
CONFIG_DIR="/etc/skeleton/mcp"
BACKUP_DIR=""
COMMITTED=0
MAX_INSTALL_SOURCE_BYTES=$((1024 * 1024))

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
  "$REPO_ROOT/scripts/home_edge_media_source_snapshot_signer.py"
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

validate_install_source_file() {
  local path="$1"
  if [[ -L "$path" || ! -f "$path" || ! -r "$path" ]]; then
    printf 'BLOCKED: unsafe controller installer source file\n' >&2
    exit 2
  fi
  local size
  size="$(wc -c < "$path")"
  if [[ ! "$size" =~ ^[0-9]+$ || "$size" -gt "$MAX_INSTALL_SOURCE_BYTES" ]]; then
    printf 'BLOCKED: controller installer source file is outside bounded reviewed size\n' >&2
    exit 2
  fi
}

atomic_install_file() {
  local source="$1"
  local target="$2"
  local mode="$3"
  local target_dir
  local tmp
  target_dir="$(dirname "$target")"
  validate_install_source_file "$source"
  mkdir -p "$target_dir"
  chown root:root "$target_dir"
  chmod 0755 "$target_dir"
  tmp="$(mktemp "${target}.tmp.XXXXXX")"
  install -o root -g root -m "$mode" "$source" "$tmp"
  mv -f "$tmp" "$target"
}

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
  if [[ -e "$BACKUP_DIR/home_edge_media_source_snapshot_signer.py" ]]; then
    install -o root -g root -m 0755 \
      "$BACKUP_DIR/home_edge_media_source_snapshot_signer.py" \
      "$INSTALL_LIB/scripts/home_edge_media_source_snapshot_signer.py"
  else
    rm -f "$INSTALL_LIB/scripts/home_edge_media_source_snapshot_signer.py"
  fi
  rm -rf "$BACKUP_DIR"
  exit "$rc"
}
trap rollback EXIT

mkdir -p "$CONFIG_DIR" "$INSTALL_LIB/scripts"
chown root:root "$INSTALL_LIB" "$INSTALL_LIB/scripts"
chmod 0755 "$INSTALL_LIB" "$INSTALL_LIB/scripts"
for name in skeleton-home-edge-exec-mcp skeleton-home-edge-exec-probe; do
  if [[ -e "$INSTALL_BIN/$name" ]]; then
    cp -a "$INSTALL_BIN/$name" "$BACKUP_DIR/$name"
  fi
done
if [[ -e "$CONFIG_DIR/skeleton-home-edge-exec.json" ]]; then
  cp -a "$CONFIG_DIR/skeleton-home-edge-exec.json" "$BACKUP_DIR/skeleton-home-edge-exec.json"
fi
if [[ -e "$INSTALL_LIB/scripts/home_edge_media_source_snapshot_signer.py" ]]; then
  cp -a "$INSTALL_LIB/scripts/home_edge_media_source_snapshot_signer.py" \
    "$BACKUP_DIR/home_edge_media_source_snapshot_signer.py"
fi

atomic_install_file \
  "$REPO_ROOT/scripts/home_edge_exec_mcp_launcher.sh" \
  "$INSTALL_BIN/skeleton-home-edge-exec-mcp" 0755
atomic_install_file \
  "$REPO_ROOT/scripts/home_edge_exec_mcp_probe.py" \
  "$INSTALL_BIN/skeleton-home-edge-exec-probe" 0755
atomic_install_file \
  "$REPO_ROOT/scripts/home_edge_media_source_snapshot_signer.py" \
  "$INSTALL_LIB/scripts/home_edge_media_source_snapshot_signer.py" 0755
atomic_install_file \
  "$REPO_ROOT/config/mcp/skeleton-home-edge-exec.json" \
  "$CONFIG_DIR/skeleton-home-edge-exec.json" 0644

COMMITTED=1
rm -rf "$BACKUP_DIR"
trap - EXIT

printf 'DONE: realtime Home Edge stdio MCP controller installed\n'
printf 'registration_config=%s\n' "$CONFIG_DIR/skeleton-home-edge-exec.json"
printf 'next=register this stdio MCP server in the actual Jeeves/Skeleton tool host\n'
