#!/usr/bin/env bash
set -euo pipefail

HOST="agent@49.12.76.236"
SSH_KEY="${HOME}/.ssh/hetzner_agent_runner_ed25519"
REMOTE_COMMIT="b0829e74b3e9c8e3019efb00f6a6b439fe917db4"
REMOTE_URL="https://raw.githubusercontent.com/alanua/Skeleton/${REMOTE_COMMIT}/scripts/operator/pr_2826_bitwarden_generic_broker_canary_remote_v1.py"

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
echo 'TARGET_MAIN=a08a3922ac7e01c32226bb193a6f072c4662a81f'
echo 'CANARY=BITWARDEN_GENERIC_CREDENTIAL_BROKER'
echo 'SECRET_INPUT=NOT_REQUIRED'
echo 'PUBLIC_OUTPUT=SANITIZED_ONLY'

set +e
ssh "${SSH_OPTS[@]}" "$HOST" "set -euo pipefail; t=\$(mktemp /tmp/pr2826-bitwarden-broker-v1.XXXXXX.py); trap 'rm -f \"\$t\"' EXIT; curl -fsSL '$REMOTE_URL' -o \"\$t\"; chmod 700 \"\$t\"; exec python3 \"\$t\"" </dev/null
rc=$?
set -e

echo 'RETURNED_TO_DE_PC=1'
echo "REMOTE_RC=${rc}"
exit 0
