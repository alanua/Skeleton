#!/usr/bin/env bash
set -euo pipefail

prefix="/"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      prefix="${2:?missing --root value}"
      shift 2
      ;;
    *)
      echo "usage: $0 [--root ROOT]" >&2
      exit 2
      ;;
  esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="${repo_root}/scripts/home_edge_display_power_off_signer.py"
install_root="${prefix%/}/usr/local/libexec/skeleton/home-edge-display-off-controller-signer"
current="${install_root}/current"
release="${install_root}/releases/$(date -u +%Y%m%dT%H%M%SZ)-$$"
sudoers_src="${repo_root}/scripts/skeleton-home-edge-display-off-controller-signer.sudoers"
sudoers_dst="${prefix%/}/etc/sudoers.d/skeleton-home-edge-display-off-controller-signer"

if [[ ! -f "$src" ]]; then
  echo "missing signer source" >&2
  exit 1
fi

umask 022
install -d -m 0755 "${install_root}/releases"
install -d -m 0755 "$release"
install -m 0755 "$src" "${release}/home_edge_display_power_off_signer.py"
/usr/bin/python3 -m py_compile "${release}/home_edge_display_power_off_signer.py"

tmp_link="${install_root}/.current.$$"
ln -s "releases/$(basename "$release")" "$tmp_link"
mv -Tf "$tmp_link" "$current"

install -d -m 0750 "$(dirname "$sudoers_dst")"
install -m 0440 "$sudoers_src" "$sudoers_dst"
if command -v visudo >/dev/null 2>&1; then
  visudo -cf "$sudoers_dst" >/dev/null
fi

echo "installed ${current}/home_edge_display_power_off_signer.py"
