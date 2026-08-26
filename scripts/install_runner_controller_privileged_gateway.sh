#!/usr/bin/env bash
set -Eeuo pipefail

DESTDIR=""
RUNNER_USER="agent"
SSH_USER="skeleton-runner-gateway"
SSH_PUBLIC_KEY=""
INSTALL_ROOT="/usr/local/lib/skeleton/runner-controller"
EXEC_ROOT="/usr/local/libexec/skeleton/runner-controller"
STATE_ROOT="/var/lib/skeleton/runner-controller"
SUDOERS_PATH="/etc/sudoers.d/skeleton-runner-controller-privileged-gateway"
SSHD_FRAGMENT="/etc/ssh/sshd_config.d/skeleton-runner-controller-privileged-gateway.conf"
SSH_AUTHORIZED_KEYS="/var/lib/skeleton/runner-controller/ssh/authorized_keys"

usage() {
  cat <<'EOF'
Usage: sudo scripts/install_runner_controller_privileged_gateway.sh [--destdir PATH] [--ssh-public-key KEY]

Installs the one-bootstrap Skeleton Runner privileged gateway files into an
isolated root when --destdir is supplied, or into the host when run by an
operator as root. The script does not start services, reload sshd, or execute a
privileged action. The optional SSH key is installed only for the dedicated
forced-command gateway user and does not change global root SSH policy.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --destdir)
      DESTDIR="${2:?missing value for --destdir}"
      shift 2
      ;;
    --ssh-public-key)
      SSH_PUBLIC_KEY="${2:?missing value for --ssh-public-key}"
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

install -d -m 0755 "$(target "$INSTALL_ROOT/core")" "$(target "$EXEC_ROOT")"
install -d -m 0700 "$(target "$STATE_ROOT")"
printf '%s\n' '# installed runner-controller gateway package' > "$(target "$INSTALL_ROOT/core/__init__.py")"
chmod 0444 "$(target "$INSTALL_ROOT/core/__init__.py")"
install -m 0444 "$repo_root/core/runner_controller_privileged_gateway.py" \
  "$(target "$INSTALL_ROOT/core/runner_controller_privileged_gateway.py")"
install -m 0444 "$repo_root/core/runner_repository_maintenance_executor.py" \
  "$(target "$INSTALL_ROOT/core/runner_repository_maintenance_executor.py")"
install -m 0555 "$repo_root/scripts/runner_controller_privileged_gateway.py" \
  "$(target "$EXEC_ROOT/privileged-gateway")"

install -d -m 0755 "$(target "$INSTALL_ROOT/config")" "$(target "$INSTALL_ROOT/schemas")"
install -m 0444 "$repo_root/RUNNER_PRIVILEGED_ACTIONS.yaml" \
  "$(target "$INSTALL_ROOT/config/RUNNER_PRIVILEGED_ACTIONS.yaml")"
cat > "$(target "$INSTALL_ROOT/config/CAPABILITY_REGISTRY.yaml")" <<'EOF'
version: "1.0.0"
capabilities:
  runner_controller_privileged_gateway:
    status: available
    module: core/runner_controller_privileged_gateway.py
    live_runtime_execution: true
    protected: true
    requires:
      - core/runner_controller_privileged_gateway.py
      - core/runner_repository_maintenance_executor.py
      - scripts/runner_controller_privileged_gateway.py
      - scripts/install_runner_controller_privileged_gateway.sh
      - RUNNER_PRIVILEGED_ACTIONS.yaml
      - schemas/runner_controller_privileged_request.schema.json
      - schemas/runner_controller_privileged_receipt.schema.json
      - docs/RUNNER_CONTROLLER_PRIVILEGED_GATEWAY.md
    tested: true
    added: "2026-08-25"
    description: Protected no-argument sudo gateway with root-owned installed trust anchors and public-safe receipts.
EOF
chmod 0444 "$(target "$INSTALL_ROOT/config/CAPABILITY_REGISTRY.yaml")"
install -m 0444 "$repo_root/schemas/runner_controller_privileged_request.schema.json" \
  "$(target "$INSTALL_ROOT/schemas/runner_controller_privileged_request.schema.json")"
install -m 0444 "$repo_root/schemas/runner_controller_privileged_receipt.schema.json" \
  "$(target "$INSTALL_ROOT/schemas/runner_controller_privileged_receipt.schema.json")"
cat > "$(target "$INSTALL_ROOT/config/checkout.json")" <<'EOF'
{"schema":"skeleton.runner_controller_checkout_config.v1","repository":"alanua/Skeleton","checkout_path":"/home/agent/agent-dev/repos/Skeleton"}
EOF
chmod 0444 "$(target "$INSTALL_ROOT/config/checkout.json")"

install -d -m 0755 "$(dirname -- "$(target "$SUDOERS_PATH")")"
cat > "$(target "$SUDOERS_PATH")" <<EOF
$RUNNER_USER ALL=(root) NOPASSWD: $EXEC_ROOT/privileged-gateway ""
EOF
chmod 0440 "$(target "$SUDOERS_PATH")"

install -d -m 0755 "$(dirname -- "$(target "$SSHD_FRAGMENT")")"
cat > "$(target "$SSHD_FRAGMENT")" <<EOF
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

if [[ -n "$SSH_PUBLIC_KEY" ]]; then
  case "$SSH_PUBLIC_KEY" in
    ssh-ed25519\ *|ecdsa-sha2-nistp256\ *) ;;
    *)
      printf 'BLOCKED: unapproved ssh public key type\n' >&2
      exit 2
      ;;
  esac
  install -d -m 0700 "$(dirname -- "$(target "$SSH_AUTHORIZED_KEYS")")"
  cat > "$(target "$SSH_AUTHORIZED_KEYS")" <<EOF
command="$EXEC_ROOT/privileged-gateway --forced-command",no-pty,no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-user-rc $SSH_PUBLIC_KEY
EOF
  chmod 0600 "$(target "$SSH_AUTHORIZED_KEYS")"
fi

printf 'DONE: Runner controller privileged gateway files installed inertly\n'
printf 'gateway=%s\n' "$EXEC_ROOT/privileged-gateway"
printf 'sudoers=%s\n' "$SUDOERS_PATH"
printf 'sshd_fragment=%s\n' "$SSHD_FRAGMENT"
