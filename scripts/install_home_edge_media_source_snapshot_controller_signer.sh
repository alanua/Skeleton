#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  install_home_edge_media_source_snapshot_controller_signer.sh --runner-user USER [--root DIR]
  install_home_edge_media_source_snapshot_controller_signer.sh --uninstall [--root DIR]

Installs only the controller-side fixed signer for:
  /usr/local/sbin/home_edge_media_source_snapshot_signer

The signer accepts no argv. It reads a minimal JSON request on stdin, reads only
/etc/skeleton/home-edge-executor-controller.env through the reviewed strict
resolver, and emits one signed HomeEdgeExecRequest envelope for
home_edge_01_media_source_snapshot_v1. It does not execute Home Edge transport
or write the private snapshot artifact.
USAGE
}

ROOT=""
RUNNER_USER=""
UNINSTALL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      ROOT="${2:?--root requires a directory}"
      shift 2
      ;;
    --runner-user)
      RUNNER_USER="${2:?--runner-user requires a username}"
      shift 2
      ;;
    --uninstall)
      UNINSTALL=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unsupported argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
prefix="${ROOT%/}"
sbin_dir="${prefix}/usr/local/sbin"
lib_dir="${prefix}/usr/local/lib/skeleton-home-edge-media-source-snapshot-signer"
sudoers_dir="${prefix}/etc/sudoers.d"
sudoers_file="${sudoers_dir}/skeleton-home-edge-media-source-snapshot-signer"
signer="${sbin_dir}/home_edge_media_source_snapshot_signer"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

runtime_path() {
  local path="$1"
  if [[ -n "$ROOT" ]]; then
    printf '%s%s' "$prefix" "$path"
  else
    printf '%s' "$path"
  fi
}

backup_path() {
  local path="$1"
  if [[ -e "$path" || -L "$path" ]]; then
    cp -a "$path" "${path}.bak.${timestamp}"
  fi
}

chown_root_if_possible() {
  if [[ -z "$ROOT" && "$(id -u)" -eq 0 ]]; then
    chown root:root "$@"
  fi
}

validate_user_token() {
  local value="$1"
  if [[ ! "$value" =~ ^[A-Za-z_][A-Za-z0-9_.-]*[$]?$ ]]; then
    echo "--runner-user contains unsupported characters" >&2
    exit 2
  fi
}

install_python_files() {
  mkdir -p "$lib_dir/core/home_edge" "$lib_dir/scripts"
  printf '%s\n' '"""Minimal installed package for the Home Edge media snapshot signer."""' > "$lib_dir/core/__init__.py"
  chmod 0644 "$lib_dir/core/__init__.py"
  install -m 0644 "$repo_root/core/home_edge/executor.py" "$lib_dir/core/home_edge/executor.py"
  install -m 0644 "$repo_root/core/home_edge/executor_gateway.py" "$lib_dir/core/home_edge/executor_gateway.py"
  install -m 0644 "$repo_root/core/home_edge/profile.py" "$lib_dir/core/home_edge/profile.py"
  install -m 0644 "$repo_root/core/home_edge/media_source_snapshot.py" "$lib_dir/core/home_edge/media_source_snapshot.py"
  install -m 0755 "$repo_root/scripts/home_edge_media_source_snapshot_signer.py" "$lib_dir/scripts/home_edge_media_source_snapshot_signer.py"
  python3 -m py_compile \
    "$lib_dir/core/home_edge/executor.py" \
    "$lib_dir/core/home_edge/executor_gateway.py" \
    "$lib_dir/core/home_edge/profile.py" \
    "$lib_dir/core/home_edge/media_source_snapshot.py" \
    "$lib_dir/scripts/home_edge_media_source_snapshot_signer.py"
  PYTHONPATH="$lib_dir" python3 - <<'PY'
from core.home_edge.media_source_snapshot import sign_snapshot_request_from_controller_stdin
assert callable(sign_snapshot_request_from_controller_stdin)
PY
}

install_wrapper() {
  mkdir -p "$sbin_dir"
  chown_root_if_possible "$sbin_dir"
  backup_path "$signer"
  cat > "$signer" <<WRAPPER
#!/usr/bin/env bash
set -euo pipefail
if [[ "\$#" -ne 0 ]]; then
  echo "home_edge_media_source_snapshot_signer accepts no argv" >&2
  exit 2
fi
python_root="$(runtime_path "/usr/local/lib/skeleton-home-edge-media-source-snapshot-signer")"
exec env -i \\
  PATH="/usr/sbin:/usr/bin:/sbin:/bin" \\
  LANG="C.UTF-8" \\
  PYTHONPATH="\$python_root" \\
  /usr/bin/python3 "\$python_root/scripts/home_edge_media_source_snapshot_signer.py"
WRAPPER
  chmod 0555 "$signer"
  chown_root_if_possible "$signer"
}

install_sudoers_rule() {
  mkdir -p "$sudoers_dir"
  chown_root_if_possible "$sudoers_dir"
  chmod 0755 "$sudoers_dir"
  backup_path "$sudoers_file"
  umask 077
  {
    printf '# Managed by skeleton Home Edge media source snapshot controller signer installer.\n'
    printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/home_edge_media_source_snapshot_signer\n' "$RUNNER_USER"
  } > "$sudoers_file"
  chmod 0440 "$sudoers_file"
  chown_root_if_possible "$sudoers_file"
  if command -v visudo >/dev/null 2>&1; then
    visudo -cf "$sudoers_file" >/dev/null
  fi
}

uninstall_signer() {
  rm -f "$signer"
  rm -f "$sudoers_file"
  rm -rf "$lib_dir"
  echo "home_edge media source snapshot controller signer uninstalled"
}

if [[ "$UNINSTALL" -eq 1 ]]; then
  uninstall_signer
  exit 0
fi

if [[ -z "$RUNNER_USER" ]]; then
  echo "--runner-user is required" >&2
  exit 2
fi
validate_user_token "$RUNNER_USER"

install_python_files
install_wrapper
install_sudoers_rule

echo "home_edge media source snapshot controller signer installed"
