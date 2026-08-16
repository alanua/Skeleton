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
echo 'BOOTSTRAP_FIX=INCLUDE_UNTRACKED_EXPECTED_FILES'
echo 'PROTECTED_MERGE=NOT_AUTHORIZED_BY_THIS_RUN'

set +e
ssh "${SSH_OPTS[@]}" "$HOST" 'bash -s' <<'REMOTE'
set -euo pipefail
t=$(mktemp /tmp/issue2834-deterministic-v2.XXXXXX.py)
trap 'rm -f "$t"' EXIT
curl -fsSL 'https://raw.githubusercontent.com/alanua/Skeleton/e610d77e72ae39a56d30f5243055e56244849f67/scripts/operator/issue_2834_deterministic_activation_remote_v1.py' -o "$t"
python3 - "$t" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
text = p.read_text(encoding='utf-8')
old = "        create_files()\n        passed, skipped = validate()\n"
new = "        create_files()\n        git('add', '-N', '--', *sorted(EXPECTED_FILES), cwd=WORKTREE)\n        passed, skipped = validate()\n"
if text.count(old) != 1:
    raise SystemExit('bootstrap_preimage_mismatch')
p.write_text(text.replace(old, new, 1), encoding='utf-8')
PY
python3 -m py_compile "$t"
exec python3 "$t"
REMOTE
rc=$?
set -e

echo 'RETURNED_TO_PHONE=1'
echo "REMOTE_RC=${rc}"
exit 0
