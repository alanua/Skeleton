#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
INSTALL_BIN="/usr/local/bin"
RUNTIME_ROOT="/opt/skeleton-home-edge-esp-lab/stage1"
COMMITTED=0
BACKUP_DIR=""
STAGING_DIR=""

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
  "$REPO_ROOT/core/__init__.py"
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
STAGING_DIR="$(mktemp -d /tmp/skeleton-home-edge-esp-lab-stage1.XXXXXX)"
rollback() {
  local rc=$?
  if [[ $COMMITTED -eq 1 ]]; then
    rm -rf "$BACKUP_DIR"
    rm -rf "$STAGING_DIR"
    return
  fi
  if [[ -e "$BACKUP_DIR/runtime" ]]; then
    rm -rf "$RUNTIME_ROOT"
    mkdir -p "$(dirname -- "$RUNTIME_ROOT")"
    cp -a "$BACKUP_DIR/runtime" "$RUNTIME_ROOT"
  else
    rm -rf "$RUNTIME_ROOT"
  fi
  for name in skeleton-home-edge-esp-lab skeleton-home-edge-esp-lab-windows-connector; do
    if [[ -e "$BACKUP_DIR/$name" ]]; then
      install -o root -g root -m 0755 "$BACKUP_DIR/$name" "$INSTALL_BIN/$name"
    else
      rm -f "$INSTALL_BIN/$name"
    fi
  done
  rm -rf "$BACKUP_DIR"
  rm -rf "$STAGING_DIR"
  exit "$rc"
}
trap rollback EXIT

mkdir -p "$INSTALL_BIN"
if [[ -e "$RUNTIME_ROOT" ]]; then
  cp -a "$RUNTIME_ROOT" "$BACKUP_DIR/runtime"
fi
for name in skeleton-home-edge-esp-lab skeleton-home-edge-esp-lab-windows-connector; do
  if [[ -e "$INSTALL_BIN/$name" ]]; then
    cp -a "$INSTALL_BIN/$name" "$BACKUP_DIR/$name"
  fi
done

mkdir -p "$STAGING_DIR/core/home_edge"
install -o root -g root -m 0644 "$REPO_ROOT/core/__init__.py" "$STAGING_DIR/core/__init__.py"
install -o root -g root -m 0644 "$REPO_ROOT/core/home_edge/esp_lab.py" "$STAGING_DIR/core/home_edge/esp_lab.py"
install -o root -g root -m 0644 "$REPO_ROOT/core/home_edge/esp_lab_connector.py" "$STAGING_DIR/core/home_edge/esp_lab_connector.py"
mkdir -p "$STAGING_DIR/schemas"
install -o root -g root -m 0644 \
  "$REPO_ROOT/schemas/home_edge_esp_lab_receipt.schema.json" \
  "$STAGING_DIR/schemas/home_edge_esp_lab_receipt.schema.json"
install -o root -g root -m 0644 \
  "$REPO_ROOT/schemas/home_edge_esp_lab_activation_receipt.schema.json" \
  "$STAGING_DIR/schemas/home_edge_esp_lab_activation_receipt.schema.json"

python3 - "$STAGING_DIR" <<'PY'
import importlib
import sys

sys.path.insert(0, sys.argv[1])
for module in ("core.home_edge.esp_lab", "core.home_edge.esp_lab_connector"):
    importlib.import_module(module)
PY

rm -rf "$RUNTIME_ROOT"
mkdir -p "$(dirname -- "$RUNTIME_ROOT")"
cp -a "$STAGING_DIR" "$RUNTIME_ROOT"
chown -R root:root "$RUNTIME_ROOT"
find "$RUNTIME_ROOT" -type d -exec chmod 0555 {} +
find "$RUNTIME_ROOT" -type f -exec chmod 0444 {} +
chmod -R a-w "$RUNTIME_ROOT"

tmp_linux="$(mktemp "$INSTALL_BIN/skeleton-home-edge-esp-lab.XXXXXX")"
tmp_connector="$(mktemp "$INSTALL_BIN/skeleton-home-edge-esp-lab-windows-connector.XXXXXX")"
cat >"$tmp_linux" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
RUNTIME_ROOT="/opt/skeleton-home-edge-esp-lab/stage1"
PYTHONPATH="$RUNTIME_ROOT" exec python3 -m core.home_edge.esp_lab "$@"
EOF
cat >"$tmp_connector" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
RUNTIME_ROOT="/opt/skeleton-home-edge-esp-lab/stage1"
PYTHONPATH="$RUNTIME_ROOT" exec python3 -m core.home_edge.esp_lab_connector "$@"
EOF
install -o root -g root -m 0755 "$tmp_linux" "$INSTALL_BIN/skeleton-home-edge-esp-lab"
install -o root -g root -m 0755 "$tmp_connector" "$INSTALL_BIN/skeleton-home-edge-esp-lab-windows-connector"
rm -f "$tmp_linux" "$tmp_connector"

"$INSTALL_BIN/skeleton-home-edge-esp-lab" discover --sysfs-root /nonexistent >/dev/null
"$INSTALL_BIN/skeleton-home-edge-esp-lab" --help >/dev/null

COMMITTED=1
rm -rf "$BACKUP_DIR"
rm -rf "$STAGING_DIR"
trap - EXIT

printf 'DONE: Home Edge ESP Lab Stage 1 read-only runtime installed\n'
printf 'stage=stage1_read_only_connector\n'
printf 'runtime_root=%s\n' "$RUNTIME_ROOT"
printf 'immutable_runtime=true\n'
printf 'self_contained_runtime=true\n'
printf 'destructive_operations_enabled=false\n'
printf 'live_device_mutation_attempted=false\n'
printf 'next=submit an explicitly approved read-only ESP Lab job when a board is attached\n'
