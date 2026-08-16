#!/usr/bin/env bash
set -euo pipefail
HOST="agent@49.12.76.236"
SSH_KEY="${HOME}/.ssh/hetzner_agent_runner_ed25519"
REMOTE_COMMIT="5b46a339a5fb01101a06296c21e9d4948b355106"
REMOTE_URL="https://raw.githubusercontent.com/alanua/Skeleton/${REMOTE_COMMIT}/scripts/operator/pr_2814_openhands_kimi_canary_remote_v8.py"

if [[ ! -f "$SSH_KEY" ]]; then
  echo 'RESULT=BLOCKED:de_pc_ssh_key_missing'
  exit 0
fi
chmod 600 "$SSH_KEY" 2>/dev/null || true
SSH_OPTS=(-i "$SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes)
if ! ssh "${SSH_OPTS[@]}" "$HOST" "test \"\$(hostname)\" = hetzner-agent-runner-1 && test \"\$(id -un)\" = agent" </dev/null; then
  echo 'RESULT=BLOCKED:hetzner_ssh_key_access'
  exit 0
fi
echo 'SSH_PREFLIGHT=PASS'
echo 'SECRET_INPUT=NOT_REQUIRED'
echo 'CANARY_MODEL=moonshotai/kimi-k2'
echo 'CANARY_BUDGET_MAX_USD=0.05'
ssh "${SSH_OPTS[@]}" "$HOST" "set -euo pipefail; t=\$(mktemp /tmp/pr2814-kimi-v8.XXXXXX.py); trap 'rm -f \"\$t\"' EXIT; curl -fsSL '$REMOTE_URL' -o \"\$t\"; chmod 700 \"\$t\"; exec python3 \"\$t\""
