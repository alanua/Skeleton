#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
LOCAL_REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
REPO_ROOT="${SKELETON_HOME_EDGE_REPO_ROOT:-$LOCAL_REPO_ROOT}"
PROFILE_ENV="${SKELETON_HOME_EDGE_PROFILE_ENV:-/etc/skeleton/home-edge-01.env}"
CONTROLLER_ENV="${SKELETON_HOME_EDGE_CONTROLLER_ENV:-/etc/skeleton/home-edge-executor-controller.env}"

for required in "$PROFILE_ENV" "$CONTROLLER_ENV" "$REPO_ROOT/scripts/home_edge_exec_mcp.py"; do
  if [[ ! -r "$required" ]]; then
    printf 'home-edge MCP launcher: required runtime input is unavailable\n' >&2
    exit 2
  fi
done

set -a
# shellcheck disable=SC1090
source "$PROFILE_ENV"
# shellcheck disable=SC1090
source "$CONTROLLER_ENV"
set +a

cd "$REPO_ROOT"
exec /usr/bin/python3 "$REPO_ROOT/scripts/home_edge_exec_mcp.py"
