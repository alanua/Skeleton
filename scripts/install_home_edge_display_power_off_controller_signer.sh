#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  install_home_edge_display_power_off_controller_signer.sh --controller-user USER [--root DIR]
  install_home_edge_display_power_off_controller_signer.sh --uninstall [--root DIR]

Installs only the controller-side fixed signer:
  /usr/local/sbin/home_edge_display_power_off_signer

It never installs or modifies the Home Edge node executor and never sources env files.
USAGE
}

ROOT=""
CONTROLLER_USER=""
UNINSTALL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="${2:?--root requires a directory}"; shift 2 ;;
    --controller-user) CONTROLLER_USER="${2:?--controller-user requires a username}"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unsupported argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
prefix="${ROOT%/}"
sbin_dir="${prefix}/usr/local/sbin"
lib_dir="${prefix}/usr/local/lib/skeleton-home-edge-display-power-off-signer"
sudoers_dir="${prefix}/etc/sudoers.d"
sudoers_file="${sudoers_dir}/skeleton-home-edge-display-power-off-signer"
signer="${sbin_dir}/home_edge_display_power_off_signer"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

backup_path() {
  local path="$1"
  if [[ -e "$path" || -L "$path" ]]; then cp -a "$path" "${path}.bak.${timestamp}"; fi
}

chown_root_if_possible() {
  if [[ -z "$ROOT" && "$(id -u)" -eq 0 ]]; then chown root:root "$@"; fi
}

validate_user_token() {
  local value="$1"
  if [[ ! "$value" =~ ^[A-Za-z_][A-Za-z0-9_.-]*[$]?$ ]]; then
    echo "--controller-user contains unsupported characters" >&2
    exit 2
  fi
}

install_python_files() {
  mkdir -p "$lib_dir/core/home_edge" "$lib_dir/scripts"
  printf '%s\n' '"""Minimal installed package for fixed Home Edge display-off signing."""' > "$lib_dir/core/__init__.py"
  printf '%s\n' '"""Home Edge package."""' > "$lib_dir/core/home_edge/__init__.py"
  install -m 0644 "$repo_root/core/home_edge/executor.py" "$lib_dir/core/home_edge/executor.py"
  install -m 0644 "$repo_root/core/home_edge/executor_gateway.py" "$lib_dir/core/home_edge/executor_gateway.py"
  install -m 0644 "$repo_root/core/home_edge/profile.py" "$lib_dir/core/home_edge/profile.py"
  install -m 0644 "$repo_root/core/home_edge/controller_auth.py" "$lib_dir/core/home_edge/controller_auth.py"
  install -m 0644 "$repo_root/core/home_edge/display_power_off.py" "$lib_dir/core/home_edge/display_power_off.py"
  install -m 0755 "$repo_root/scripts/home_edge_display_power_off_signer.py" "$lib_dir/scripts/home_edge_display_power_off_signer.py"
  python3 -m py_compile \
    "$lib_dir/core/home_edge/executor.py" \
    "$lib_dir/core/home_edge/executor_gateway.py" \
    "$lib_dir/core/home_edge/profile.py" \
    "$lib_dir/core/home_edge/controller_auth.py" \
    "$lib_dir/core/home_edge/display_power_off.py" \
    "$lib_dir/scripts/home_edge_display_power_off_signer.py"
  PYTHONPATH="$lib_dir" python3 - <<'PY'
from core.home_edge.display_power_off import signer_envelope_from_stdin
assert callable(signer_envelope_from_stdin)
PY
}

install_wrapper() {
  mkdir -p "$sbin_dir"
  chown_root_if_possible "$sbin_dir"
  backup_path "$signer"
  cat > "$signer" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$#" -ne 0 ]]; then
  echo "home_edge_display_power_off_signer accepts no argv" >&2
  exit 2
fi
python_root="/usr/local/lib/skeleton-home-edge-display-power-off-signer"
exec env -i \
  PATH="/usr/sbin:/usr/bin:/sbin:/bin" \
  LANG="C.UTF-8" \
  PYTHONPATH="$python_root" \
  /usr/bin/python3 "$python_root/scripts/home_edge_display_power_off_signer.py"
WRAPPER
  if [[ -n "$ROOT" ]]; then
    sed -i "s#python_root=\"/usr/local/lib#python_root=\"${prefix}/usr/local/lib#" "$signer"
  fi
  chmod 0555 "$signer"
  chown_root_if_possible "$signer"
}

install_sudoers_rule() {
  mkdir -p "$sudoers_dir"
  chmod 0755 "$sudoers_dir"
  chown_root_if_possible "$sudoers_dir"
  backup_path "$sudoers_file"
  umask 077
  {
    printf '# Managed by Skeleton Home Edge display-off controller signer installer.\n'
    printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/home_edge_display_power_off_signer\n' "$CONTROLLER_USER"
  } > "$sudoers_file"
  chmod 0440 "$sudoers_file"
  chown_root_if_possible "$sudoers_file"
  if command -v visudo >/dev/null 2>&1; then visudo -cf "$sudoers_file" >/dev/null; fi
}

if [[ "$UNINSTALL" -eq 1 ]]; then
  rm -f "$signer" "$sudoers_file"
  rm -rf "$lib_dir"
  echo "home_edge display power off controller signer uninstalled"
  exit 0
fi

if [[ -z "$CONTROLLER_USER" ]]; then echo "--controller-user is required" >&2; exit 2; fi
validate_user_token "$CONTROLLER_USER"
install_python_files
install_wrapper
install_sudoers_rule
echo "home_edge display power off controller signer installed"
