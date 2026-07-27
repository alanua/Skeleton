#!/usr/bin/env bash
set -euo pipefail
original="${SSH_ORIGINAL_COMMAND:-}"
if [[ "$original" =~ ^/usr/local/bin/skeleton-resolver-receive\ (resolver-[A-Za-z0-9_.-]+-[0-9]{8}T[0-9]{6}Z\.jsonl\.gz)\ ([a-f0-9]{64})$ ]]; then
  exec /usr/local/bin/skeleton-resolver-receive "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
fi
echo "resolver receive command rejected" >&2
exit 126
