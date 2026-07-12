#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" != "--server" || "$#" -ne 1 ]]; then
  echo "home_edge_exec_root supports only --server" >&2
  exit 2
fi

env_file="/etc/skeleton/home_edge_executor.env"
python_root="/usr/local/lib/skeleton-home-edge-executor"
server_script="${python_root}/scripts/home_edge_exec.py"

if [[ ! -r "$env_file" ]]; then
  echo "home_edge_exec private environment is missing" >&2
  exit 2
fi
if [[ ! -r "$server_script" ]]; then
  echo "home_edge_exec server is missing" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
. "$env_file"
set +a

exec env -i \
  PATH="/usr/sbin:/usr/bin:/sbin:/bin" \
  LANG="${LANG:-C.UTF-8}" \
  LC_ALL="${LC_ALL:-}" \
  SKELETON_HOME_EDGE_EXEC_HMAC_SECRET="${SKELETON_HOME_EDGE_EXEC_HMAC_SECRET:?}" \
  SKELETON_HOME_EDGE_DESKTOP_USER="${SKELETON_HOME_EDGE_DESKTOP_USER:?}" \
  SKELETON_HOME_EDGE_EXEC_AUDIT_LOG="${SKELETON_HOME_EDGE_EXEC_AUDIT_LOG:?}" \
  SKELETON_HOME_EDGE_EXEC_IDEMPOTENCY_CACHE="${SKELETON_HOME_EDGE_EXEC_IDEMPOTENCY_CACHE:?}" \
  SKELETON_HOME_EDGE_EXEC_CANCEL_DIR="${SKELETON_HOME_EDGE_EXEC_CANCEL_DIR:?}" \
  PYTHONPATH="$python_root" \
  /usr/bin/env python3 "$server_script" --server
