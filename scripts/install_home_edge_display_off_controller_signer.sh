#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="/home/agent/agent-dev/repos/Skeleton"
INSTALL_PATH="/usr/local/sbin/skeleton-home-edge-display-off-controller-signer"
SUDOERS_DIR="/etc/sudoers.d"
SUDOERS_FILE="${SUDOERS_DIR}/skeleton-home-edge-display-off-controller-signer"
RUNNER_USER="agent"
BACKUP_DIR=""
COMMITTED=0

usage() {
  printf '%s\n' 'Usage: sudo scripts/install_home_edge_display_off_controller_signer.sh [--repo-root PATH] [--runner-user USER]'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root)
      REPO_ROOT="${2:?missing value for --repo-root}"
      shift 2
      ;;
    --runner-user)
      RUNNER_USER="${2:?missing value for --runner-user}"
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
if ! id "$RUNNER_USER" >/dev/null 2>&1; then
  printf 'BLOCKED: runner user unavailable\n' >&2
  exit 2
fi

required=(
  "$REPO_ROOT/scripts/home_edge_display_off_controller_signer.py"
  "$REPO_ROOT/core/home_edge/display_power_off.py"
  "$REPO_ROOT/core/home_edge/controller_auth.py"
  "$REPO_ROOT/core/home_edge/executor.py"
  "$REPO_ROOT/core/home_edge/executor_gateway.py"
  "/etc/skeleton/home-edge-01.env"
  "/etc/skeleton/home-edge-executor-controller.env"
)
for path in "${required[@]}"; do
  if [[ ! -r "$path" ]]; then
    printf 'BLOCKED: required controller input is unavailable\n' >&2
    exit 2
  fi
done

BACKUP_DIR="$(mktemp -d /tmp/skeleton-display-off-signer.XXXXXX)"
rollback() {
  local rc=$?
  if [[ $COMMITTED -eq 1 ]]; then
    rm -rf "$BACKUP_DIR"
    return
  fi
  if [[ -e "$BACKUP_DIR/signer" ]]; then
    install -o root -g root -m 0755 "$BACKUP_DIR/signer" "$INSTALL_PATH"
  else
    rm -f "$INSTALL_PATH"
  fi
  if [[ -e "$BACKUP_DIR/sudoers" ]]; then
    install -o root -g root -m 0440 "$BACKUP_DIR/sudoers" "$SUDOERS_FILE"
  else
    rm -f "$SUDOERS_FILE"
  fi
  rm -rf "$BACKUP_DIR"
  exit "$rc"
}
trap rollback EXIT

mkdir -p "$SUDOERS_DIR"
if [[ -e "$INSTALL_PATH" ]]; then
  cp -a "$INSTALL_PATH" "$BACKUP_DIR/signer"
fi
if [[ -e "$SUDOERS_FILE" ]]; then
  cp -a "$SUDOERS_FILE" "$BACKUP_DIR/sudoers"
fi

install -o root -g root -m 0755 \
  "$REPO_ROOT/scripts/home_edge_display_off_controller_signer.py" \
  "$INSTALL_PATH"
printf '%s ALL=(root) NOPASSWD: %s\n' "$RUNNER_USER" "$INSTALL_PATH" > "$SUDOERS_FILE"
chmod 0440 "$SUDOERS_FILE"
chown root:root "$SUDOERS_FILE"
if command -v visudo >/dev/null 2>&1; then
  visudo -cf "$SUDOERS_FILE" >/dev/null
fi

PYTHONPATH="$REPO_ROOT" "$INSTALL_PATH" <<'EOF' >/dev/null
{"idempotency_key":"home-edge-display-off-controller-boundary-20260809-v1","operator_approval_ref":"EXPLICIT_2026_08_09_TURN_OFF_HOME_EDGE_MONITOR","schema":"skeleton.home_edge.display_power_off.signer_input.v1","target_node":"home-edge-01","task_id":"home_edge_01_display_power_off_v1"}
EOF

COMMITTED=1
rm -rf "$BACKUP_DIR"
trap - EXIT

printf 'DONE: display-off controller signer installed and verified\n'
printf 'signer=%s\n' "$INSTALL_PATH"
printf 'sudoers=%s\n' "$SUDOERS_FILE"
