#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="${1:-/home/agent/agent-dev/worktrees/skeleton/issue-1772}"
cd "$REPO_ROOT"

python3 -m py_compile \
  scripts/home_edge_exec_mcp.py \
  scripts/home_edge_exec_mcp_probe.py

bash -n \
  scripts/home_edge_exec_mcp_launcher.sh \
  scripts/install_home_edge_realtime_controller.sh

python3 -m pytest -q \
  tests/test_home_edge_executor_gateway.py \
  tests/test_home_edge_realtime_controller.py

git diff --check main...HEAD

printf 'DONE: issue 1772 validation passed\n'
