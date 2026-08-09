#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="/home/agent/agent-dev/repos/Skeleton"
INSTALL_BIN="/usr/local/sbin"
SUDOERS_DIR="/etc/sudoers.d"
SIGNER="/usr/local/sbin/skeleton-home-edge-media-source-snapshot-signer"
SUDOERS_FILE="/etc/sudoers.d/skeleton-home-edge-media-source-snapshot-signer"
RUNNER_USER="${SUDO_USER:-${USER:-}}"

usage() {
  cat <<'EOF'
Usage: sudo scripts/install_home_edge_media_source_snapshot_controller_signer.sh [--repo-root PATH] [--runner-user USER]

Installs the controller-side fixed Home Edge media source snapshot signer.
The signer reads the controller HMAC only from the fixed private controller env,
signs exactly one home_edge_01_media_source_snapshot_v1 executor request, and
never executes transport or reads/copies the Home Edge source artifact.
EOF
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
if [[ ! "$RUNNER_USER" =~ ^[A-Za-z_][A-Za-z0-9_.-]*[$]?$ ]]; then
  printf 'BLOCKED: runner user is invalid\n' >&2
  exit 2
fi

required=(
  "$REPO_ROOT/core/home_edge/media_source_snapshot.py"
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

install -o root -g root -m 0755 -d "$INSTALL_BIN" "$SUDOERS_DIR"
tmp="$(mktemp /tmp/skeleton-media-source-snapshot-signer.XXXXXX)"
cleanup() {
  rm -f "$tmp"
}
trap cleanup EXIT

{
  printf '#!/usr/bin/env bash\n'
  printf 'set -Eeuo pipefail\n'
  printf 'if [[ "$#" -ne 0 ]]; then\n'
  printf '  echo "snapshot signer accepts no arguments" >&2\n'
  printf '  exit 2\n'
  printf 'fi\n'
  printf 'exec env -i PATH="/usr/sbin:/usr/bin:/sbin:/bin" LANG="${LANG:-C.UTF-8}" PYTHONPATH=%q /usr/bin/env python3 -m core.home_edge.media_source_snapshot\n' "$REPO_ROOT"
} > "$tmp"
install -o root -g root -m 0555 "$tmp" "$SIGNER"

umask 077
{
  printf '# Managed by skeleton Home Edge media source snapshot signer installer.\n'
  printf '%s ALL=(root) NOPASSWD: %s\n' "$RUNNER_USER" "$SIGNER"
} > "$tmp"
install -o root -g root -m 0440 "$tmp" "$SUDOERS_FILE"
if command -v visudo >/dev/null 2>&1; then
  visudo -cf "$SUDOERS_FILE" >/dev/null
fi

"$SIGNER" < <(printf '{"maintenance_task_id":"home_edge_01_media_source_snapshot_v1"}\n') >/dev/null
python3 - <<'PY'
import importlib

module = importlib.import_module("core.home_edge.media_source_snapshot")
assert module.TASK_ID == "home_edge_01_media_source_snapshot_v1"
assert module.SIGNER_COMMAND == (
    "/usr/bin/sudo",
    "-n",
    "--",
    "/usr/local/sbin/skeleton-home-edge-media-source-snapshot-signer",
)
PY

printf 'DONE: Home Edge media source snapshot controller signer installed and verified\n'
printf 'signer=%s\n' "$SIGNER"
printf 'sudoers=%s\n' "$SUDOERS_FILE"
