#!/usr/bin/env bash
set -euo pipefail

HOST="agent@49.12.76.236"
SSH_KEY="${HOME}/.ssh/hetzner_agent_runner_ed25519"
HELPER_COMMIT="31cbd946c3b47769006e75ce2eae3db97b89e05a"
HELPER_URL="https://raw.githubusercontent.com/alanua/Skeleton/${HELPER_COMMIT}/scripts/operator/pr_2814_runtime_activate_remote.py"

BWS_TOKEN=""
SECRET_REF=""
OPENROUTER_KEY=""
PROJECT_ID=""
PRIVATE_JSON=""

cleanup() {
  BWS_TOKEN=""
  SECRET_REF=""
  OPENROUTER_KEY=""
  PROJECT_ID=""
  PRIVATE_JSON=""
  unset BWS_TOKEN SECRET_REF OPENROUTER_KEY PROJECT_ID PRIVATE_JSON || true
  exec 3>&- 3<&- 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

if [[ ! -r /dev/tty || ! -w /dev/tty ]]; then
  echo 'RESULT=BLOCKED:interactive_tty_required'
  exit 0
fi
exec 3<>/dev/tty

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
)

if ! ssh "${SSH_OPTS[@]}" "$HOST" \
  "test \"\$(hostname)\" = hetzner-agent-runner-1 && test \"\$(id -un)\" = agent" \
  </dev/null; then
  echo 'RESULT=BLOCKED:hetzner_ssh_key_access'
  exit 0
fi

echo 'SSH_PREFLIGHT=PASS'

printf 'Bitwarden machine access token: ' >&3
IFS= read -r -s BWS_TOKEN <&3
printf '\n' >&3
if [[ -z "${BWS_TOKEN:-}" ]]; then
  echo 'RESULT=BLOCKED:bitwarden_machine_token_required'
  exit 0
fi

printf 'Existing Bitwarden OpenRouter secret UUID (Enter = create/update from key): ' >&3
IFS= read -r SECRET_REF <&3
if [[ -z "${SECRET_REF:-}" ]]; then
  printf 'OpenRouter API key: ' >&3
  IFS= read -r -s OPENROUTER_KEY <&3
  printf '\n' >&3
  if [[ -z "${OPENROUTER_KEY:-}" ]]; then
    echo 'RESULT=BLOCKED:openrouter_key_required'
    exit 0
  fi
  printf 'Bitwarden project UUID (Enter = create Skeleton Runtime project): ' >&3
  IFS= read -r PROJECT_ID <&3
fi

PRIVATE_JSON="$({
  printf '%s\0%s\0%s\0%s\0' \
    "${BWS_TOKEN:-}" "${SECRET_REF:-}" "${OPENROUTER_KEY:-}" "${PROJECT_ID:-}"
} | python3 -c '
import json,sys
parts=sys.stdin.buffer.read().split(b"\0")
if len(parts) < 5:
    raise SystemExit(2)
keys=("bws_token","secret_ref","openrouter_key","project_id")
obj={k:parts[i].decode("utf-8") for i,k in enumerate(keys)}
sys.stdout.write(json.dumps(obj,separators=(",",":")))
')"

BWS_TOKEN=""
SECRET_REF=""
OPENROUTER_KEY=""
PROJECT_ID=""
unset BWS_TOKEN SECRET_REF OPENROUTER_KEY PROJECT_ID

set +e
printf '%s' "$PRIVATE_JSON" | ssh \
  "${SSH_OPTS[@]}" \
  "$HOST" \
  "set -euo pipefail; t=\$(mktemp /tmp/pr2814-runtime-activate.XXXXXX.py); trap 'rm -f \"\$t\"' EXIT; curl -fsSL '$HELPER_URL' -o \"\$t\"; chmod 700 \"\$t\"; exec python3 \"\$t\""
rc=$?
set -e
PRIVATE_JSON=""
unset PRIVATE_JSON

echo 'RETURNED_TO_DE_PC=1'
echo "REMOTE_RC=$rc"
exit "$rc"
