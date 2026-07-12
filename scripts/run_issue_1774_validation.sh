#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="${1:-/home/agent/agent-dev/worktrees/skeleton/issue-1774}"
cd "$REPO_ROOT"

PYCACHE_DIR="$(mktemp -d /tmp/skeleton-issue-1774-pycache.XXXXXX)"
trap 'rm -rf "$PYCACHE_DIR"' EXIT

PYTHONPYCACHEPREFIX="$PYCACHE_DIR" python3 -m py_compile \
  scripts/home_edge_control_mcp.py \
  scripts/home_edge_control_mcp_probe.py

bash -n \
  scripts/home_edge_control_mcp_launcher.sh \
  scripts/install_home_edge_media_control.sh \
  scripts/run_issue_1774_validation.sh

python3 -m pytest -q \
  tests/test_home_edge_executor_gateway.py \
  tests/test_home_edge_realtime_controller.py \
  tests/test_home_edge_control_mcp.py

git diff --check main...HEAD

printf 'DONE: issue 1774 validation passed\n'
