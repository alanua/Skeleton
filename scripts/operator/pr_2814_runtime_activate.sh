#!/usr/bin/env bash
set -euo pipefail

HOST="agent@49.12.76.236"
HELPER_COMMIT="31cbd946c3b47769006e75ce2eae3db97b89e05a"
HELPER_URL="https://raw.githubusercontent.com/alanua/Skeleton/${HELPER_COMMIT}/scripts/operator/pr_2814_runtime_activate_remote.py"

cleanup() {
  unset BWS_TOKEN SECRET_REF OPENROUTER_KEY PROJECT_ID PRIVATE_JSON || true
}
trap cleanup EXIT

printf 'Bitwarden machine access token: ' >&2
IFS= read -r -s BWS_TOKEN
printf '\n' >&2
if [[ -z "${BWS_TOKEN}" ]]; then
  echo 'RESULT=BLOCKED:bitwarden_machine_token_required'
  exit 0
fi

printf 'Existing Bitwarden OpenRouter secret UUID (Enter = create/update from key): ' >&2
IFS= read -r SECRET_REF
OPENROUTER_KEY=""
PROJECT_ID=""
if [[ -z "${SECRET_REF}" ]]; then
  printf 'OpenRouter API key: ' >&2
  IFS= read -r -s OPENROUTER_KEY
  printf '\n' >&2
  if [[ -z "${OPENROUTER_KEY}" ]]; then
    echo 'RESULT=BLOCKED:openrouter_key_required'
    exit 0
  fi
  printf 'Bitwarden project UUID (Enter = create Skeleton Runtime project): ' >&2
  IFS= read -r PROJECT_ID
fi

PRIVATE_JSON="$({
  printf '%s\0%s\0%s\0%s\0' "$BWS_TOKEN" "$SECRET_REF" "$OPENROUTER_KEY" "$PROJECT_ID"
} | python3 -c '
import json,sys
parts=sys.stdin.buffer.read().split(b"\0")
if len(parts) < 5: raise SystemExit(2)
keys=("bws_token","secret_ref","openrouter_key","project_id")
obj={k:parts[i].decode("utf-8") for i,k in enumerate(keys)}
sys.stdout.write(json.dumps(obj,separators=(",",":")))
')"

# Clear shell variables containing secret material as early as possible. The serialized
# payload remains only in this process memory until it is written to SSH stdin.
unset BWS_TOKEN SECRET_REF OPENROUTER_KEY PROJECT_ID

set +e
printf '%s' "$PRIVATE_JSON" | ssh \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=yes \
  "$HOST" \
  "set -euo pipefail; t=\$(mktemp /tmp/pr2814-runtime-activate.XXXXXX.py); trap 'rm -f \"\$t\"' EXIT; curl -fsSL '$HELPER_URL' -o \"\$t\"; chmod 700 \"\$t\"; exec python3 \"\$t\""
rc=$?
set -e
PRIVATE_JSON=""
unset PRIVATE_JSON

echo 'RETURNED_TO_TERMUX=1'
echo "REMOTE_RC=$rc"
exit "$rc"
