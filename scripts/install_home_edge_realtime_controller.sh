#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="/home/agent/agent-dev/repos/Skeleton"
INSTALL_BIN="/usr/local/bin"
INSTALL_LIB="/usr/local/lib/skeleton-home-edge-controller"
IMMUTABLE_BOOTSTRAP="/usr/local/lib/skeleton-home-edge-controller/bootstrap/install_home_edge_realtime_controller.sh"
CONFIG_DIR="/etc/skeleton/mcp"
BACKUP_DIR=""
COMMITTED=0
RUNNER_SERVICE_USER="agent"
MAX_INSTALL_SOURCE_BYTES=$((1024 * 1024))
INSTALLED_BOOTSTRAP_MODE=0
declare -A EXPECTED_SOURCE_SHA256=(
  ["scripts/home_edge_exec_mcp_launcher.sh"]="ebf17a8c7b0c0d0e45d620b596e6e5806805e94d3f42ac8c46bd87c2a58b606b"
  ["scripts/home_edge_exec_mcp_probe.py"]="77ab294c2942ad8df85323ad4a64edf68783bdc60e6d8c0af21ca59ccec19391"
  ["scripts/home_edge_media_source_snapshot_signer.py"]="46c5e0d4494dd16b65accb6171207406d06dd686827bfe91c36e0cb0b53b4e10"
  ["config/mcp/skeleton-home-edge-exec.json"]="aca728c51415a36f5e26b87ef59e2aee4b6664eae0b923f815a1de40fe69ce1b"
  ["core/__init__.py"]="30316aeec194bdfe2ca70c941dc3822393e29e2ad8ad25c5bf701e325224e30a"
  ["core/home_edge/executor.py"]="d985b8a74672cbb54d9f6490aefb01fe8eba4860b573e9a4b1d2186560fad05f"
  ["core/home_edge/executor_gateway.py"]="849ec5733dbe76d99e083d4aef4301fecb8db6db3dbb4bab6b830c720ffef788"
  ["core/home_edge/media_source_snapshot.py"]="3a8a0a3f4d3cd339916a2b1b8832cd920ce6a37d8625cb65d9f2527bf7713997"
)

usage() {
  cat <<'EOF'
Usage: sudo scripts/install_home_edge_realtime_controller.sh [--repo-root PATH]

Installs the stdio MCP launcher, public-safe health probe, immutable controller
bootstrap, and protected snapshot signer on the trusted controller. The reviewed
installer may be copied from a checkout only as inert data; root execution is
accepted only from /usr/local/lib/skeleton-home-edge-controller/bootstrap.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --installed-bootstrap)
      INSTALLED_BOOTSTRAP_MODE=1
      shift
      ;;
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
if [[ $INSTALLED_BOOTSTRAP_MODE -ne 1 ]]; then
  printf 'BLOCKED: root must execute only the immutable installed controller bootstrap\n' >&2
  exit 2
fi
SELF_PATH="$(/usr/bin/python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$0")"
if [[ "$SELF_PATH" != "$IMMUTABLE_BOOTSTRAP" ]]; then
  printf 'BLOCKED: checkout controller installer cannot execute as root\n' >&2
  exit 2
fi
if [[ -L "$IMMUTABLE_BOOTSTRAP" || ! -f "$IMMUTABLE_BOOTSTRAP" ]]; then
  printf 'BLOCKED: immutable controller bootstrap is unsafe\n' >&2
  exit 2
fi
bootstrap_mode="$(stat -c '%a:%U:%G' "$IMMUTABLE_BOOTSTRAP")"
if [[ "$bootstrap_mode" != "500:root:root" ]]; then
  printf 'BLOCKED: immutable controller bootstrap ownership or mode mismatch\n' >&2
  exit 2
fi

required=(
  "$REPO_ROOT/scripts/home_edge_exec_mcp.py"
  "$REPO_ROOT/scripts/home_edge_exec_mcp_launcher.sh"
  "$REPO_ROOT/scripts/home_edge_exec_mcp_probe.py"
  "$REPO_ROOT/scripts/home_edge_media_source_snapshot_signer.py"
  "$REPO_ROOT/core/__init__.py"
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
  local rel
  target_dir="$(dirname "$target")"
  validate_install_source_file "$source"
  mkdir -p "$target_dir"
  chown root:root "$target_dir"
  chmod 0755 "$target_dir"
  tmp="$(mktemp "${target}.tmp.XXXXXX")"
  install -o root -g root -m "$mode" "$source" "$tmp"
  local source_hash target_hash
  source_hash="$(sha256sum "$source" | awk '{print $1}')"
  rel="${source#"$REPO_ROOT"/}"
  if [[ "${EXPECTED_SOURCE_SHA256[$rel]+set}" == "set" && "${EXPECTED_SOURCE_SHA256[$rel]}" != "$source_hash" ]]; then
    rm -f "$tmp"
    printf 'BLOCKED: reviewed controller source hash mismatch\n' >&2
    exit 2
  fi
  target_hash="$(sha256sum "$tmp" | awk '{print $1}')"
  if [[ "$source_hash" != "$target_hash" ]]; then
    rm -f "$tmp"
    printf 'BLOCKED: inert controller file copy hash mismatch\n' >&2
    exit 2
  fi
  mv -f "$tmp" "$target"
}

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

atomic_install_file \
  "$REPO_ROOT/scripts/home_edge_exec_mcp_launcher.sh" \
  "$INSTALL_BIN/skeleton-home-edge-exec-mcp" 0755
atomic_install_file \
  "$REPO_ROOT/scripts/home_edge_exec_mcp_probe.py" \
  "$INSTALL_BIN/skeleton-home-edge-exec-probe" 0755
atomic_install_file \
  "$REPO_ROOT/config/mcp/skeleton-home-edge-exec.json" \
  "$CONFIG_DIR/skeleton-home-edge-exec.json" 0644

mkdir -p "$INSTALL_LIB/bootstrap" "$INSTALL_LIB/core/home_edge" "$INSTALL_LIB/scripts"
chown root:root "$INSTALL_LIB" "$INSTALL_LIB/bootstrap" "$INSTALL_LIB/core" "$INSTALL_LIB/core/home_edge" "$INSTALL_LIB/scripts"
chmod 0755 "$INSTALL_LIB" "$INSTALL_LIB/core" "$INSTALL_LIB/core/home_edge" "$INSTALL_LIB/scripts"
chmod 0755 "$INSTALL_LIB/bootstrap"
atomic_install_file "$SELF_PATH" "$IMMUTABLE_BOOTSTRAP" 0500
atomic_install_file "$REPO_ROOT/core/__init__.py" "$INSTALL_LIB/core/__init__.py" 0644
atomic_install_file "$REPO_ROOT/core/home_edge/executor.py" "$INSTALL_LIB/core/home_edge/executor.py" 0644
atomic_install_file "$REPO_ROOT/core/home_edge/executor_gateway.py" "$INSTALL_LIB/core/home_edge/executor_gateway.py" 0644
atomic_install_file "$REPO_ROOT/core/home_edge/media_source_snapshot.py" "$INSTALL_LIB/core/home_edge/media_source_snapshot.py" 0644
atomic_install_file "$REPO_ROOT/scripts/home_edge_media_source_snapshot_signer.py" "$INSTALL_LIB/scripts/home_edge_media_source_snapshot_signer.py" 0755

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

/usr/bin/python3 -m py_compile \
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
