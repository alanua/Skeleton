#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  install_home_edge_snapshot_signer.sh --runner-user USER [--root DIR]
  install_home_edge_snapshot_signer.sh --uninstall [--root DIR]

Installs the fixed-purpose Home Edge media source snapshot signer. The signer
accepts no argv, reads one unsigned request JSON object from stdin, signs only
the home_edge_01_media_source_snapshot_v1 request, and exits. It does not run
transport and does not read the media source artifact.
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

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
prefix="${ROOT%/}"
sbin_dir="${prefix}/usr/local/sbin"
lib_parent="${prefix}/usr/local/lib"
lib_dir="${lib_parent}/skeleton-home-edge-snapshot-signer"
sudoers_dir="${prefix}/etc/sudoers.d"
sudoers_file="${sudoers_dir}/skeleton-home-edge-snapshot-signer"
signer="${sbin_dir}/home_edge_media_source_snapshot_signer"
runtime_lib_dir="/usr/local/lib/skeleton-home-edge-snapshot-signer"
if [[ -n "$ROOT" ]]; then
  runtime_lib_dir="${prefix}/usr/local/lib/skeleton-home-edge-snapshot-signer"
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

validate_user_token() {
  local value="$1"
  if [[ ! "$value" =~ ^[A-Za-z_][A-Za-z0-9_.-]*[$]?$ ]]; then
    echo "--runner-user contains unsupported characters" >&2
    exit 2
  fi
}

chown_root_if_possible() {
  if [[ -z "$ROOT" && "$(id -u)" -eq 0 ]]; then
    chown root:root "$@"
  fi
}

reject_unsafe_path() {
  local path="$1"
  local parent
  parent="$(dirname "$path")"
  if [[ -L "$path" || -L "$parent" ]]; then
    echo "unsafe symlinked install path: $path" >&2
    exit 2
  fi
}

reject_unsafe_source() {
  local path="$1"
  if [[ ! -f "$path" || -L "$path" ]]; then
    echo "unsafe signer source path: $path" >&2
    exit 2
  fi
  if [[ "$repo_root" != "$(cd "$repo_root" && pwd -P)" ]]; then
    echo "unsafe signer source root" >&2
    exit 2
  fi
}

validate_install_tree() {
  local root="$1"
  while IFS= read -r -d '' path; do
    if [[ -L "$path" ]]; then
      echo "install tree contains symlink: $path" >&2
      exit 2
    fi
    local mode
    mode="$(stat -c '%a' "$path")"
    if [[ "$mode" =~ [2367]$ || "$mode" =~ [2367].$ ]]; then
      echo "install tree path is group/world writable: $path" >&2
      exit 2
    fi
    if [[ -z "$ROOT" && "$(stat -c '%u:%g' "$path")" != "0:0" ]]; then
      echo "install tree path is not root-owned: $path" >&2
      exit 2
    fi
  done < <(find "$root" -mindepth 0 -print0)
}

install_payload() {
  reject_unsafe_source "$repo_root/core/home_edge/executor.py"
  reject_unsafe_source "$repo_root/core/home_edge/executor_gateway.py"
  reject_unsafe_source "$repo_root/core/home_edge/profile.py"
  reject_unsafe_source "$repo_root/core/home_edge/media_source_snapshot.py"
  reject_unsafe_source "$repo_root/scripts/home_edge_media_source_snapshot_signer.py"

  mkdir -p "$lib_parent" "$sbin_dir"
  reject_unsafe_path "$lib_dir"
  reject_unsafe_path "$signer"
  chown_root_if_possible "$lib_parent" "$sbin_dir"
  chmod 0755 "$lib_parent" "$sbin_dir"

  local tmp_dir
  tmp_dir="$(mktemp -d "${lib_parent}/.skeleton-home-edge-snapshot-signer.XXXXXX")"
  trap 'rm -rf "$tmp_dir"' RETURN
  mkdir -p "$tmp_dir/core/home_edge" "$tmp_dir/scripts"
  printf '%s\n' '"""Immutable installed package for the Home Edge snapshot signer."""' > "$tmp_dir/core/__init__.py"
  printf '%s\n' '"""Home Edge snapshot signer runtime."""' > "$tmp_dir/core/home_edge/__init__.py"
  install -m 0644 "$repo_root/core/home_edge/executor.py" "$tmp_dir/core/home_edge/executor.py"
  install -m 0644 "$repo_root/core/home_edge/executor_gateway.py" "$tmp_dir/core/home_edge/executor_gateway.py"
  install -m 0644 "$repo_root/core/home_edge/profile.py" "$tmp_dir/core/home_edge/profile.py"
  install -m 0644 "$repo_root/core/home_edge/media_source_snapshot.py" "$tmp_dir/core/home_edge/media_source_snapshot.py"
  install -m 0755 "$repo_root/scripts/home_edge_media_source_snapshot_signer.py" "$tmp_dir/scripts/home_edge_media_source_snapshot_signer.py"
  sed -i "s#Path(\"/usr/local/lib/skeleton-home-edge-snapshot-signer\")#Path(\"$runtime_lib_dir\")#" \
    "$tmp_dir/scripts/home_edge_media_source_snapshot_signer.py"
  if [[ -n "$ROOT" ]]; then
    sed -i \
      -e "s#Path(\"/etc/skeleton\")#Path(\"$prefix/etc/skeleton\")#" \
      -e "s#Path(\"/etc/skeleton/home-edge-01.env\")#Path(\"$prefix/etc/skeleton/home-edge-01.env\")#" \
      -e "s#Path(\"/etc/skeleton/home-edge-executor-controller.env\")#Path(\"$prefix/etc/skeleton/home-edge-executor-controller.env\")#" \
      -e "s#Path(\"/usr/local/sbin/home_edge_media_source_snapshot_signer\")#Path(\"$prefix/usr/local/sbin/home_edge_media_source_snapshot_signer\")#" \
      "$tmp_dir/core/home_edge/media_source_snapshot.py"
  fi
  python3 -m py_compile \
    "$tmp_dir/core/home_edge/executor.py" \
    "$tmp_dir/core/home_edge/executor_gateway.py" \
    "$tmp_dir/core/home_edge/profile.py" \
    "$tmp_dir/core/home_edge/media_source_snapshot.py" \
    "$tmp_dir/scripts/home_edge_media_source_snapshot_signer.py"
  chmod -R go-w "$tmp_dir"
  find "$tmp_dir" -type d -exec chmod 0755 {} +
  find "$tmp_dir" -type f -exec chmod u=rw,go=r {} +
  chmod 0755 "$tmp_dir/scripts/home_edge_media_source_snapshot_signer.py"
  chown_root_if_possible "$tmp_dir"
  chown_root_if_possible -R "$tmp_dir"
  validate_install_tree "$tmp_dir"

  if [[ -e "$lib_dir" || -L "$lib_dir" ]]; then
    if [[ -L "$lib_dir" ]]; then
      echo "refusing to replace symlinked signer install tree" >&2
      exit 2
    fi
    mv "$lib_dir" "${lib_dir}.bak.${timestamp}"
  fi
  mv "$tmp_dir" "$lib_dir"
  trap - RETURN
  validate_install_tree "$lib_dir"
}

install_executable() {
  reject_unsafe_path "$signer"
  rm -f "$signer"
  install -m 0555 "$lib_dir/scripts/home_edge_media_source_snapshot_signer.py" "$signer"
  chown_root_if_possible "$signer"
  validate_install_tree "$signer"
}

install_sudoers_rule() {
  mkdir -p "$sudoers_dir"
  reject_unsafe_path "$sudoers_file"
  chown_root_if_possible "$sudoers_dir"
  chmod 0755 "$sudoers_dir"
  umask 077
  {
    printf '# Managed by skeleton Home Edge snapshot signer installer.\n'
    printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/home_edge_media_source_snapshot_signer\n' "$RUNNER_USER"
  } > "$sudoers_file"
  chmod 0440 "$sudoers_file"
  chown_root_if_possible "$sudoers_file"
  if command -v visudo >/dev/null 2>&1; then
    visudo -cf "$sudoers_file" >/dev/null
  fi
}

uninstall_signer() {
  rm -f "$signer" "$sudoers_file"
  if [[ -d "$lib_dir" && ! -L "$lib_dir" ]]; then
    mv "$lib_dir" "${lib_dir}.bak.${timestamp}"
  fi
  echo "home_edge_media_source_snapshot_signer uninstalled"
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
if ! getent passwd "$RUNNER_USER" >/dev/null; then
  echo "runner account cannot be resolved" >&2
  exit 2
fi

install_payload
install_executable
install_sudoers_rule

echo "home_edge_media_source_snapshot_signer installed"
