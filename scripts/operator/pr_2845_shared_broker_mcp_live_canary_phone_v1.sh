#!/usr/bin/env bash
set -euo pipefail

HOST="agent@49.12.76.236"
REMOTE_COMMIT="b7df325da83162471c27de1ca8912440f0068d71"
REMOTE_URL="https://raw.githubusercontent.com/alanua/Skeleton/${REMOTE_COMMIT}/scripts/operator/pr_2845_shared_broker_mcp_live_canary_remote_v1.py"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=yes)

if ! ssh "${SSH_OPTS[@]}" "$HOST" 'test "$(hostname)" = hetzner-agent-runner-1 && test "$(id -un)" = agent' </dev/null; then
  echo 'RESULT=BLOCKED:hetzner_ssh_access'
  exit 0
fi

echo 'SSH_PREFLIGHT=PASS'
echo 'TARGET_PR=2845'
echo 'TARGET_MAIN=799b189acbbdf41bfeb7031df6becd2f6cd86ca2'
echo 'CANARY_SCOPE=SHARED_BROKER_OPENHANDS_PLUS_CREDENTIAL_MCP'
echo 'SECRET_INPUT=NOT_REQUIRED'

set +e
ssh "${SSH_OPTS[@]}" "$HOST" "set -euo pipefail; t=\$(mktemp /tmp/pr2845-live-canary-v1.XXXXXX.py); trap 'rm -f \"\$t\"' EXIT; curl -fsSL '$REMOTE_URL' -o \"\$t\"; chmod 700 \"\$t\"; exec python3 \"\$t\"" </dev/null
rc=$?
set -e

echo 'RETURNED_TO_PHONE=1'
echo "REMOTE_RC=${rc}"
exit 0
