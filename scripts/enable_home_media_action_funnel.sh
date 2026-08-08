#!/usr/bin/env bash
set -Eeuo pipefail

PORT="8765"

usage() {
  cat <<'EOF'
Usage: sudo scripts/enable_home_media_action_funnel.sh

Enables a Tailscale Funnel from public HTTPS to the localhost-only bounded Home
Media Action API. The API still requires its separate Bearer key for status and
control endpoints. /health and /openapi.json contain no private state.
EOF
}

if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
  usage
  exit 0
fi
if [[ $# -ne 0 ]]; then
  usage >&2
  exit 2
fi
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  printf 'BLOCKED: Funnel setup must run as root\n' >&2
  exit 2
fi
if ! command -v tailscale >/dev/null 2>&1; then
  printf 'BLOCKED: tailscale CLI is unavailable\n' >&2
  exit 2
fi

curl -fsS --connect-timeout 1 --max-time 2 "http://127.0.0.1:${PORT}/health" >/dev/null

tailscale status >/dev/null
tailscale funnel --bg "$PORT"

printf 'FUNNEL_STATUS_BEGIN\n'
tailscale funnel status
printf 'FUNNEL_STATUS_END\n'
printf 'DONE: bounded Home Media Action API Funnel enabled\n'
