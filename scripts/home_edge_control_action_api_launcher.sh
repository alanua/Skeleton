#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="${SKELETON_HOME_EDGE_REPO_ROOT:-/home/agent/agent-dev/repos/Skeleton}"
PROFILE_ENV="${SKELETON_HOME_EDGE_PROFILE_ENV:-/etc/skeleton/home-edge-01.env}"
CONTROLLER_ENV="${SKELETON_HOME_EDGE_CONTROLLER_ENV:-/etc/skeleton/home-edge-executor-controller.env}"
ACTION_ENV="${SKELETON_HOME_MEDIA_ACTION_ENV:-/etc/skeleton/home-media-action.env}"
SERVER="$REPO_ROOT/scripts/home_edge_control_action_api.py"

for required in "$PROFILE_ENV" "$CONTROLLER_ENV" "$ACTION_ENV" "$SERVER"; do
  if [[ ! -r "$required" ]]; then
    printf 'home media Action API launcher: required runtime input is unavailable\n' >&2
    exit 2
  fi
done

set -a
# shellcheck disable=SC1090
source "$PROFILE_ENV"
# shellcheck disable=SC1090
source "$CONTROLLER_ENV"
# shellcheck disable=SC1090
source "$ACTION_ENV"
set +a

cd "$REPO_ROOT"
exec env PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 "$SERVER" --host 127.0.0.1 --port 8765
