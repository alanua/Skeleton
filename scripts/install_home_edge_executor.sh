#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  install_home_edge_executor.sh --desktop-user USER [--ssh-target-user USER] [--root DIR]
  install_home_edge_executor.sh --desktop-user USER [--ssh-target-user USER] --replace-secret-stdin [--root DIR]
  install_home_edge_executor.sh --uninstall [--root DIR]

Installs the one-shot Home Edge executor for strict OpenSSH command:
  /usr/local/bin/home_edge_exec --server

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
SSH_TARGET_USER=""
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
    --ssh-target-user)
      SSH_TARGET_USER="${2:?--ssh-target-user requires a username}"
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
sbin_dir="${prefix}/usr/local/sbin"
lib_dir="${prefix}/usr/local/lib/skeleton-home-edge-executor"
env_dir="${prefix}/etc/skeleton"
sudoers_dir="${prefix}/etc/sudoers.d"
state_dir="${prefix}/var/lib/skeleton/home_edge_exec"
audit_dir="${prefix}/var/log/skeleton/home_edge_exec"
cancel_dir="${state_dir}/cancel"
env_file="${env_dir}/home_edge_executor.env"
sudoers_file="${sudoers_dir}/skeleton-home-edge-executor"
wrapper="${bin_dir}/home_edge_exec"
root_wrapper="${sbin_dir}/home_edge_exec_root"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

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
  local field="$2"
  if [[ ! "$value" =~ ^[A-Za-z_][A-Za-z0-9_.-]*[$]?$ ]]; then
    echo "$field contains unsupported characters" >&2
    exit 2
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
  chown_root_if_possible "$env_dir"
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
  chown_root_if_possible "$env_file"
}

install_wrapper() {
  mkdir -p "$bin_dir" "$sbin_dir"
  chown_root_if_possible "$bin_dir" "$sbin_dir"
  backup_path "$wrapper"
  backup_path "$root_wrapper"
  rm -f "$wrapper" "$root_wrapper"
  cat > "$wrapper" <<WRAPPER
#!/usr/bin/env bash
set -euo pipefail
if [[ "\${1:-}" != "--server" || "\$#" -ne 1 ]]; then
  echo "home_edge_exec supports only --server on the node" >&2
  exit 2
fi
exec sudo -n -- "$(runtime_path "/usr/local/sbin/home_edge_exec_root")" --server
WRAPPER
  cat > "$root_wrapper" <<ROOT_WRAPPER
#!/usr/bin/env bash
set -euo pipefail
if [[ "\${1:-}" != "--server" || "\$#" -ne 1 ]]; then
  echo "home_edge_exec_root supports only --server" >&2
  exit 2
fi
env_file="$(runtime_path "/etc/skeleton/home_edge_executor.env")"
python_root="$(runtime_path "/usr/local/lib/skeleton-home-edge-executor")"
server_script="\$python_root/scripts/home_edge_exec.py"
if [[ ! -r "\$env_file" ]]; then
  echo "home_edge_exec private environment is missing" >&2
  exit 2
fi
if [[ ! -r "\$server_script" ]]; then
  echo "home_edge_exec server is missing" >&2
  exit 2
fi
set -a
# shellcheck disable=SC1090
. "\$env_file"
set +a
exec env -i \\
  PATH="/usr/sbin:/usr/bin:/sbin:/bin" \\
  LANG="\${LANG:-C.UTF-8}" \\
  LC_ALL="\${LC_ALL:-}" \\
  SKELETON_HOME_EDGE_EXEC_HMAC_SECRET="\${SKELETON_HOME_EDGE_EXEC_HMAC_SECRET:?}" \\
  SKELETON_HOME_EDGE_DESKTOP_USER="\${SKELETON_HOME_EDGE_DESKTOP_USER:?}" \\
  SKELETON_HOME_EDGE_EXEC_AUDIT_LOG="\${SKELETON_HOME_EDGE_EXEC_AUDIT_LOG:?}" \\
  SKELETON_HOME_EDGE_EXEC_IDEMPOTENCY_CACHE="\${SKELETON_HOME_EDGE_EXEC_IDEMPOTENCY_CACHE:?}" \\
  SKELETON_HOME_EDGE_EXEC_CANCEL_DIR="\${SKELETON_HOME_EDGE_EXEC_CANCEL_DIR:?}" \\
  PYTHONPATH="\$python_root" \\
  /usr/bin/env python3 "\$server_script" --server
ROOT_WRAPPER
  chmod 0755 "$wrapper"
  chmod 0555 "$root_wrapper"
  chown_root_if_possible "$wrapper" "$root_wrapper"
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

install_sudoers_rule() {
  mkdir -p "$sudoers_dir"
  chown_root_if_possible "$sudoers_dir"
  chmod 0755 "$sudoers_dir"
  backup_path "$sudoers_file"
  rm -f "$sudoers_file"
  umask 077
  {
    printf '# Managed by skeleton Home Edge executor installer.\n'
    printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/home_edge_exec_root --server\n' "$SSH_TARGET_USER"
  } > "$sudoers_file"
  chmod 0440 "$sudoers_file"
  chown_root_if_possible "$sudoers_file"
  if command -v visudo >/dev/null 2>&1; then
    visudo -cf "$sudoers_file" >/dev/null
  fi
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
  validate_user_token "$DESKTOP_USER" "--desktop-user"
  if [[ -z "$SSH_TARGET_USER" ]]; then
    SSH_TARGET_USER="$DESKTOP_USER"
  fi
  validate_user_token "$SSH_TARGET_USER" "--ssh-target-user"
}

install_state_dirs() {
  mkdir -p "$state_dir" "$audit_dir" "$cancel_dir"
  chmod 0700 "$state_dir" "$audit_dir" "$cancel_dir"
  chown_root_if_possible "$state_dir" "$audit_dir" "$cancel_dir"
}

uninstall_executor() {
  backup_path "$env_file"
  rm -f "$wrapper"
  rm -f "$root_wrapper"
  rm -f "$sudoers_file"
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
install_sudoers_rule

echo "home_edge_exec one-shot executor installed"
