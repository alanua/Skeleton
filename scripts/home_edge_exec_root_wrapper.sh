#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" != "--server" || "$#" -ne 1 ]]; then
  echo "home_edge_exec_root supports only --server" >&2
  exit 2
fi

exec env -i \
  PATH="/usr/sbin:/usr/bin:/sbin:/bin" \
  LANG="C.UTF-8" \
  PYTHONSAFEPATH="1" \
  /usr/bin/python3 /usr/local/lib/skeleton-home-edge-executor/scripts/home_edge_exec_root_payload.py --server
