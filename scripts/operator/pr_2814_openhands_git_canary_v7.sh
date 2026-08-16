#!/usr/bin/env bash
set -euo pipefail

HOST="agent@49.12.76.236"
SSH_KEY="${HOME}/.ssh/hetzner_agent_runner_ed25519"
REMOTE_COMMIT="4c42b2002fa1b50a22c3b72ac1203021b7db2af8"
REMOTE_URL="https://raw.githubusercontent.com/alanua/Skeleton/${REMOTE_COMMIT}/scripts/operator/pr_2814_openhands_git_canary_remote_v7.py"

if [[ ! -f "$SSH_KEY" ]]; then
  echo 'RESULT=BLOCKED:de_pc_ssh_key_missing'
  exit 0
fi
chmod 600 "$SSH_KEY" 2>/dev/null || true

SSH_OPTS=(-i "$SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes)

if ! ssh "${SSH_OPTS[@]}" "$HOST" \
  'test "$(hostname)" = hetzner-agent-runner-1 && test "$(id -un)" = agent' </dev/null; then
  echo 'RESULT=BLOCKED:hetzner_ssh_key_access'
  exit 0
fi

echo 'SSH_PREFLIGHT=PASS'
echo 'SECRET_INPUT=NOT_REQUIRED'

ssh "${SSH_OPTS[@]}" "$HOST" \
  "set -euo pipefail; t=\$(mktemp /tmp/pr2814-openhands-git-v7.XXXXXX.py); trap 'rm -f \"\$t\"' EXIT; curl -fsSL '$REMOTE_URL' -o \"\$t\"; chmod 700 \"\$t\"; exec python3 \"\$t\""
