#!/usr/bin/env bash
set -euo pipefail

HOST="agent@49.12.76.236"
SSH_KEY="${HOME}/.ssh/hetzner_agent_runner_ed25519"
HELPER_COMMIT="14ef36c9fd91ac25dc7af97b83051a8ad42eeaf3"
HELPER_URL="https://raw.githubusercontent.com/alanua/Skeleton/${HELPER_COMMIT}/scripts/operator/pr_2814_runtime_repair_binding_remote_v6.py"

if [[ ! -f "$SSH_KEY" ]]; then
  echo 'RESULT=BLOCKED:de_pc_ssh_key_missing'
  echo "EXPECTED_KEY=$SSH_KEY"
  exit 0
fi
chmod 600 "$SSH_KEY" 2>/dev/null || true

SSH_OPTS=(
  -i "$SSH_KEY"
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes
)

if ! ssh "${SSH_OPTS[@]}" "$HOST" \
  "test \"\$(hostname)\" = hetzner-agent-runner-1 && test \"\$(id -un)\" = agent" \
  </dev/null; then
  echo 'RESULT=BLOCKED:hetzner_ssh_key_access'
  exit 0
fi

echo 'SSH_PREFLIGHT=PASS'
echo 'SECRET_INPUT=NOT_REQUIRED'

set +e
ssh "${SSH_OPTS[@]}" "$HOST" \
  "set -euo pipefail; t=\$(mktemp /tmp/pr2814-binding-repair.XXXXXX.py); trap 'rm -f \"\$t\"' EXIT; curl -fsSL '$HELPER_URL' -o \"\$t\"; chmod 700 \"\$t\"; exec python3 \"\$t\""
rc=$?
set -e

echo 'RETURNED_TO_DE_PC=1'
echo "REMOTE_RC=$rc"
exit "$rc"
