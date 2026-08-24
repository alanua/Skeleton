#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
INSTALL_BIN="/usr/local/bin"
COMMITTED=0
BACKUP_DIR=""

usage() {
  cat <<'EOF'
Usage: sudo scripts/install_home_edge_esp_lab.sh [--repo-root PATH]

Installs the bounded Home Edge ESP Lab Stage 1 read-only helper entrypoints.
It does not create a listener, service, control plane, credential, or ESP device
mutation path. Live board inspection remains a separate explicitly approved
read-only job.
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
  "$REPO_ROOT/scripts/home_edge_esp_lab.py"
  "$REPO_ROOT/scripts/home_edge_esp_lab_windows_connector.py"
  "$REPO_ROOT/core/home_edge/esp_lab.py"
  "$REPO_ROOT/core/home_edge/esp_lab_connector.py"
  "$REPO_ROOT/schemas/home_edge_esp_lab_receipt.schema.json"
  "$REPO_ROOT/schemas/home_edge_esp_lab_activation_receipt.schema.json"
)
for path in "${required[@]}"; do
  if [[ ! -r "$path" ]]; then
    printf 'BLOCKED: required ESP Lab Stage 1 input is unavailable\n' >&2
    exit 2
  fi
done

BACKUP_DIR="$(mktemp -d /tmp/skeleton-home-edge-esp-lab.XXXXXX)"
rollback() {
  local rc=$?
  if [[ $COMMITTED -eq 1 ]]; then
    rm -rf "$BACKUP_DIR"
    return
  fi
  for name in skeleton-home-edge-esp-lab skeleton-home-edge-esp-lab-windows-connector; do
    if [[ -e "$BACKUP_DIR/$name" ]]; then
      install -o root -g root -m 0755 "$BACKUP_DIR/$name" "$INSTALL_BIN/$name"
    else
      rm -f "$INSTALL_BIN/$name"
    fi
  done
  rm -rf "$BACKUP_DIR"
  exit "$rc"
}
trap rollback EXIT

mkdir -p "$INSTALL_BIN"
for name in skeleton-home-edge-esp-lab skeleton-home-edge-esp-lab-windows-connector; do
  if [[ -e "$INSTALL_BIN/$name" ]]; then
    cp -a "$INSTALL_BIN/$name" "$BACKUP_DIR/$name"
  fi
done

install -o root -g root -m 0755 \
  "$REPO_ROOT/scripts/home_edge_esp_lab.py" \
  "$INSTALL_BIN/skeleton-home-edge-esp-lab"
install -o root -g root -m 0755 \
  "$REPO_ROOT/scripts/home_edge_esp_lab_windows_connector.py" \
  "$INSTALL_BIN/skeleton-home-edge-esp-lab-windows-connector"

"$INSTALL_BIN/skeleton-home-edge-esp-lab" discover --sysfs-root /nonexistent >/dev/null
"$INSTALL_BIN/skeleton-home-edge-esp-lab" --help >/dev/null

COMMITTED=1
rm -rf "$BACKUP_DIR"
trap - EXIT

printf 'DONE: Home Edge ESP Lab Stage 1 read-only helpers installed\n'
printf 'stage=stage1_read_only_connector\n'
printf 'destructive_operations_enabled=false\n'
printf 'live_device_mutation_attempted=false\n'
printf 'next=submit an explicitly approved read-only ESP Lab job when a board is attached\n'
