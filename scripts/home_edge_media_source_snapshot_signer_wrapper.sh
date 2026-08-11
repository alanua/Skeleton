#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 0 ]]; then
  echo "home_edge_media_source_snapshot_signer supports no argv" >&2
  exit 2
fi

exec env -i \
  PATH="/usr/sbin:/usr/bin:/sbin:/bin" \
  LANG="C.UTF-8" \
  /usr/bin/python3 /usr/local/lib/skeleton-home-edge-executor/scripts/home_edge_media_source_snapshot_signer.py
