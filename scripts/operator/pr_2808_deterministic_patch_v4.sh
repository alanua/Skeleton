#!/usr/bin/env bash
set -euo pipefail

HOST="agent@49.12.76.236"
SSH_KEY="${HOME}/.ssh/hetzner_agent_runner_ed25519"
HELPER_COMMIT="bc83745e5fa90dcc5a27bcf33834bd4d41e887fe"
HELPER_URL="https://raw.githubusercontent.com/alanua/Skeleton/${HELPER_COMMIT}/scripts/operator/pr_2808_deterministic_patch_remote_v3.py"

if [[ ! -f "$SSH_KEY" ]]; then
  echo "RESULT=BLOCKED:de_pc_ssh_key_missing"
  echo "EXPECTED_KEY=$SSH_KEY"
  exit 0
fi
chmod 600 "$SSH_KEY" 2>/dev/null || true

SSH_OPTS=(
  -i "$SSH_KEY"
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=10
)

if ! ssh "${SSH_OPTS[@]}" "$HOST" \
  'test "$(hostname)" = hetzner-agent-runner-1 && test "$(id -un)" = agent' </dev/null; then
  echo 'RESULT=BLOCKED:hetzner_ssh_key_access'
  exit 0
fi

echo 'SSH_PREFLIGHT=PASS'
echo 'EXECUTION_MODE=DETERMINISTIC_NO_LLM'
echo 'TARGET_ISSUE=2808'
echo 'CONTRACT=RUN_NOW_FIRST_THEN_SAFE_GENERAL_FALLBACK'
echo 'BOOTSTRAP_FIX=GENERATED_TEST_RAW_STRING'

set +e
ssh "${SSH_OPTS[@]}" "$HOST" \
  "set -euo pipefail; t=\$(mktemp /tmp/pr2808-deterministic-v4.XXXXXX.py); trap 'rm -f \"\$t\"' EXIT; curl -fsSL '$HELPER_URL' -o \"\$t\"; sed -i \"s|BRANCH = 'runner/repair-zero-depth-runnow-current-main-v4'|BRANCH = 'runner/repair-zero-depth-runnow-current-main-v5'|\" \"\$t\"; sed -i \"s|^REVISED_GATES_TEST = '''def |REVISED_GATES_TEST = r'''def |\" \"\$t\"; python3 -m py_compile \"\$t\"; python3 \"\$t\"" </dev/null
rc=$?
set -e

echo 'RETURNED_TO_DE_PC=1'
echo "REMOTE_RC=$rc"
exit "$rc"
