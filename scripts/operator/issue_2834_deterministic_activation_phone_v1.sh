#!/usr/bin/env bash
set -euo pipefail

HOST="agent@49.12.76.236"
REMOTE_COMMIT="e610d77e72ae39a56d30f5243055e56244849f67"
REMOTE_URL="https://raw.githubusercontent.com/alanua/Skeleton/${REMOTE_COMMIT}/scripts/operator/issue_2834_deterministic_activation_remote_v1.py"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=yes)

if ! ssh "${SSH_OPTS[@]}" "$HOST" 'test "$(hostname)" = hetzner-agent-runner-1 && test "$(id -un)" = agent' </dev/null; then
  echo 'RESULT=BLOCKED:hetzner_ssh_access'
  exit 0
fi

echo 'SSH_PREFLIGHT=PASS'
echo 'TARGET_ISSUE=2834'
echo 'EXECUTION_MODE=DETERMINISTIC_NO_LLM'
echo 'TARGET_MAIN=a08a3922ac7e01c32226bb193a6f072c4662a81f'
echo 'PROTECTED_MERGE=NOT_AUTHORIZED_BY_THIS_RUN'

set +e
ssh "${SSH_OPTS[@]}" "$HOST" "set -euo pipefail; t=\$(mktemp /tmp/issue2834-deterministic-v1.XXXXXX.py); trap 'rm -f \"\$t\"' EXIT; curl -fsSL '$REMOTE_URL' -o \"\$t\"; chmod 700 \"\$t\"; exec python3 \"\$t\"" </dev/null
rc=$?
set -e

echo 'RETURNED_TO_PHONE=1'
echo "REMOTE_RC=${rc}"
exit 0
