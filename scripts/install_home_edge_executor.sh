#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  install_home_edge_executor.sh --desktop-user USER [--root DIR]
  install_home_edge_executor.sh --desktop-user USER --replace-secret-stdin [--root DIR]
  install_home_edge_executor.sh --uninstall [--root DIR]

Installs the one-shot Home Edge executor for strict OpenSSH command:
  home_edge_exec --server

The HMAC secret is accepted only from stdin when --replace-secret-stdin is set,
or from an existing private SKELETON_HOME_EDGE_EXEC_HMAC_SECRET environment
variable. Re-running without a replacement preserves an existing private env
file secret. The script never accepts the secret as an argv value.

Rollback:
  Existing installed files are backed up beside their target with a timestamped
  .bak suffix before replacement. --uninstall removes the wrapper and installed
  Python files but preserves the private env file as a timestamped backup.
USAGE
}

ROOT=""
DESKTOP_USER=""
REPLACE_SECRET_STDIN=0
UNINSTALL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      ROOT="${2:?--root requires a directory}"
      shift 2
      ;;
    --desktop-user)
      DESKTOP_USER="${2:?--desktop-user requires a username}"
      shift 2
      ;;
    --replace-secret-stdin)
      REPLACE_SECRET_STDIN=1
      shift
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
bin_dir="${prefix}/usr/local/bin"
lib_dir="${prefix}/usr/local/lib/skeleton-home-edge-executor"
env_dir="${prefix}/etc/skeleton"
state_dir="${prefix}/var/lib/skeleton/home_edge_exec"
audit_dir="${prefix}/var/log/skeleton/home_edge_exec"
cancel_dir="${state_dir}/cancel"
env_file="${env_dir}/home_edge_executor.env"
wrapper="${bin_dir}/home_edge_exec"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

backup_path() {
  local path="$1"
  if [[ -e "$path" || -L "$path" ]]; then
    cp -a "$path" "${path}.bak.${timestamp}"
  fi
}

quote_env() {
  local value="$1"
  printf "'%s'" "${value//\'/\'\"\'\"\'\'}"
}

runtime_path() {
  local path="$1"
  if [[ -n "$ROOT" ]]; then
    printf '%s%s' "$prefix" "$path"
  else
    printf '%s' "$path"
  fi
}

read_existing_secret() {
  if [[ ! -f "$env_file" ]]; then
    return 1
  fi
  local line
  line="$(grep -E '^SKELETON_HOME_EDGE_EXEC_HMAC_SECRET=' "$env_file" | tail -n 1 || true)"
  [[ -n "$line" ]] || return 1
  line="${line#SKELETON_HOME_EDGE_EXEC_HMAC_SECRET=}"
  if [[ "$line" == \'*\' ]]; then
    line="${line:1:${#line}-2}"
    line="${line//\'\\\'\'/\'}"
  fi
  printf '%s' "$line"
}

install_private_env() {
  local secret=""
  if [[ "$REPLACE_SECRET_STDIN" -eq 1 ]]; then
    secret="$(cat)"
    secret="${secret%$'\n'}"
  elif read_existing_secret >/dev/null; then
    secret="$(read_existing_secret)"
  elif [[ -n "${SKELETON_HOME_EDGE_EXEC_HMAC_SECRET:-}" ]]; then
    secret="$SKELETON_HOME_EDGE_EXEC_HMAC_SECRET"
  else
    echo "missing HMAC secret: provide --replace-secret-stdin or private environment" >&2
    exit 2
  fi
  if [[ -z "$secret" ]]; then
    echo "HMAC secret must not be empty" >&2
    exit 2
  fi

  mkdir -p "$env_dir"
  chmod 0700 "$env_dir"
  backup_path "$env_file"
  umask 077
  {
    printf 'SKELETON_HOME_EDGE_EXEC_HMAC_SECRET=%s\n' "$(quote_env "$secret")"
    printf 'SKELETON_HOME_EDGE_DESKTOP_USER=%s\n' "$(quote_env "$DESKTOP_USER")"
    printf 'SKELETON_HOME_EDGE_EXEC_AUDIT_LOG=%s\n' "$(quote_env "$(runtime_path "/var/log/skeleton/home_edge_exec/audit.jsonl")")"
    printf 'SKELETON_HOME_EDGE_EXEC_IDEMPOTENCY_CACHE=%s\n' "$(quote_env "$(runtime_path "/var/lib/skeleton/home_edge_exec/state.json")")"
    printf 'SKELETON_HOME_EDGE_EXEC_CANCEL_DIR=%s\n' "$(quote_env "$(runtime_path "/var/lib/skeleton/home_edge_exec/cancel")")"
  } > "$env_file"
  chmod 0600 "$env_file"
}

install_wrapper() {
  mkdir -p "$bin_dir"
  backup_path "$wrapper"
  cat > "$wrapper" <<WRAPPER
#!/usr/bin/env bash
set -euo pipefail
if [[ "\${1:-}" != "--server" || "\$#" -ne 1 ]]; then
  echo "home_edge_exec supports only --server on the node" >&2
  exit 2
fi
env_file="$(runtime_path "/etc/skeleton/home_edge_executor.env")"
if [[ ! -r "\$env_file" ]]; then
  echo "home_edge_exec private environment is missing" >&2
  exit 2
fi
set -a
# shellcheck disable=SC1090
. "\$env_file"
set +a
export PYTHONPATH="$(runtime_path "/usr/local/lib/skeleton-home-edge-executor")\${PYTHONPATH:+:\$PYTHONPATH}"
exec /usr/bin/env python3 "$(runtime_path "/usr/local/lib/skeleton-home-edge-executor/scripts/home_edge_exec.py")" --server
WRAPPER
  chmod 0755 "$wrapper"
}

install_python_files() {
  mkdir -p "$lib_dir/core/home_edge" "$lib_dir/scripts"
  printf '%s\n' '"""Minimal installed package for the Home Edge one-shot executor."""' > "$lib_dir/core/__init__.py"
  chmod 0644 "$lib_dir/core/__init__.py"
  install -m 0644 "$repo_root/core/home_edge/executor.py" "$lib_dir/core/home_edge/executor.py"
  install -m 0644 "$repo_root/core/home_edge/executor_gateway.py" "$lib_dir/core/home_edge/executor_gateway.py"
  install -m 0644 "$repo_root/core/home_edge/profile.py" "$lib_dir/core/home_edge/profile.py"
  install -m 0755 "$repo_root/scripts/home_edge_exec.py" "$lib_dir/scripts/home_edge_exec.py"
  install -m 0755 "$repo_root/scripts/home_edge_executor_server.py" "$lib_dir/scripts/home_edge_executor_server.py"
  python3 -m py_compile \
    "$lib_dir/core/home_edge/executor.py" \
    "$lib_dir/core/home_edge/executor_gateway.py" \
    "$lib_dir/scripts/home_edge_exec.py" \
    "$lib_dir/scripts/home_edge_executor_server.py"
}

validate_desktop_user() {
  if [[ -z "$DESKTOP_USER" ]]; then
    echo "--desktop-user is required" >&2
    exit 2
  fi
  if ! getent passwd "$DESKTOP_USER" >/dev/null; then
    echo "desktop account cannot be resolved" >&2
    exit 2
  fi
}

install_state_dirs() {
  mkdir -p "$state_dir" "$audit_dir" "$cancel_dir"
  chmod 0700 "$state_dir" "$audit_dir" "$cancel_dir"
  if [[ -z "$ROOT" && "$(id -u)" -eq 0 ]]; then
    chown -R root:root "$env_dir" "$state_dir" "$audit_dir"
  fi
}

uninstall_executor() {
  backup_path "$env_file"
  rm -f "$wrapper"
  rm -rf "$lib_dir"
  if [[ -f "$env_file" ]]; then
    mv "$env_file" "${env_file}.bak.${timestamp}"
  fi
  echo "home_edge_exec uninstalled; private env preserved as backup when present"
}

if [[ "$UNINSTALL" -eq 1 ]]; then
  uninstall_executor
  exit 0
fi

validate_desktop_user
install_state_dirs
install_python_files
install_private_env
install_wrapper

echo "home_edge_exec one-shot executor installed"
