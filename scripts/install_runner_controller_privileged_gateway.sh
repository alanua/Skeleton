#!/usr/bin/env bash
set -Eeuo pipefail

DESTDIR=""
RUNNER_USER="agent"
SSH_USER="skeleton-runner-gateway"
INSTALL_ROOT="/usr/local/lib/skeleton/runner-controller"
EXEC_ROOT="/usr/local/libexec/skeleton/runner-controller"
SUDOERS_PATH="/etc/sudoers.d/skeleton-runner-controller-privileged-gateway"
SSHD_FRAGMENT="/etc/ssh/sshd_config.d/skeleton-runner-controller-privileged-gateway.conf"

usage() {
  cat <<'EOF'
Usage: sudo scripts/install_runner_controller_privileged_gateway.sh [--destdir PATH]

Installs the one-bootstrap Skeleton Runner privileged gateway files into an
isolated root when --destdir is supplied, or into the host when run by an
operator as root. The script does not start services, reload sshd, or execute a
privileged action.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --destdir)
      DESTDIR="${2:?missing value for --destdir}"
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

if [[ -z "$DESTDIR" && ${EUID:-$(id -u)} -ne 0 ]]; then
  printf 'BLOCKED: installer must run as root unless --destdir is used\n' >&2
  exit 2
fi

repo_root="$(dirname -- "$(dirname -- "${BASH_SOURCE[0]}")")"
target() {
  printf '%s%s\n' "$DESTDIR" "$1"
}

install -d -m 0755 "$(target "$INSTALL_ROOT")" "$(target "$EXEC_ROOT")"
install -m 0444 "$repo_root/core/runner_controller_privileged_gateway.py" \
  "$(target "$INSTALL_ROOT/runner_controller_privileged_gateway.py")"
install -m 0555 "$repo_root/scripts/runner_controller_privileged_gateway.py" \
  "$(target "$EXEC_ROOT/privileged-gateway")"

install -d -m 0755 "$(dirname -- "$(target "$SUDOERS_PATH")")"
cat > "$(target "$SUDOERS_PATH")" <<EOF
$RUNNER_USER ALL=(root) NOPASSWD: $EXEC_ROOT/privileged-gateway
EOF
chmod 0440 "$(target "$SUDOERS_PATH")"

install -d -m 0755 "$(dirname -- "$(target "$SSHD_FRAGMENT")")"
cat > "$(target "$SSHD_FRAGMENT")" <<EOF
PermitRootLogin no
Match User $SSH_USER
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    PermitTTY no
    AllowTcpForwarding no
    X11Forwarding no
    AllowAgentForwarding no
    ForceCommand $EXEC_ROOT/privileged-gateway --forced-command
EOF
chmod 0444 "$(target "$SSHD_FRAGMENT")"

printf 'DONE: Runner controller privileged gateway files installed inertly\n'
printf 'gateway=%s\n' "$EXEC_ROOT/privileged-gateway"
printf 'sudoers=%s\n' "$SUDOERS_PATH"
printf 'sshd_fragment=%s\n' "$SSHD_FRAGMENT"
