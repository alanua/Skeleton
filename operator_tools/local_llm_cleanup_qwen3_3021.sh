#!/usr/bin/env bash
set -euo pipefail

ssh agent@49.12.76.236 'bash -s' <<'EOF'
set -euo pipefail

REPO="alanua/Skeleton"
ISSUE="3021"
MARKER="<!-- local-llm-cleanup-qwen3-v1 -->"
KEEP1="qwen2.5:1.5b"
KEEP2="nomic-embed-text:latest"
DROP="qwen3:1.7b"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

post_report() {
  local body="$1" cid
  cid="$(gh api "repos/$REPO/issues/$ISSUE/comments" --paginate --jq ".[] | select(.body | contains(\"$MARKER\")) | .id" 2>/dev/null | tail -n1 || true)"
  if [ -n "$cid" ]; then
    gh api --method PATCH "repos/$REPO/issues/comments/$cid" -f body="$body" >/dev/null
  else
    gh api --method POST "repos/$REPO/issues/$ISSUE/comments" -f body="$body" >/dev/null
  fi
}

fail() {
  post_report "$MARKER
### Local LLM cleanup
- status: BLOCKED
- reason: $1
- model_deletions: zero_or_unconfirmed
- production_routing_changed: false"
  echo "ISSUE=https://github.com/alanua/Skeleton/issues/$ISSUE"
  echo "RESULT=LOCAL_LLM_CLEANUP_PUBLISHED"
  exit 0
}

for cmd in gh python3 ollama; do
  command -v "$cmd" >/dev/null 2>&1 || fail "required_local_tool_missing"
done

inventory() {
python3 - <<'PY'
import urllib.request
with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=5) as r:
    print(r.read().decode())
PY
}

inventory >"$TMP/before.json" 2>/dev/null || fail "ollama_api_unreachable"
python3 - "$TMP/before.json" "$KEEP1" "$KEEP2" "$DROP" <<'PY' || fail "expected_precleanup_inventory_missing"
import json,sys
obj=json.load(open(sys.argv[1])); names={str(x.get('name') or x.get('model') or '') for x in obj.get('models',[])}
need=set(sys.argv[2:])
raise SystemExit(0 if need <= names else 1)
PY

FREE_BEFORE="$(df -PB1 / | awk 'NR==2 {print $4}')"

ollama stop "$DROP" >/dev/null 2>&1 || true
if ! ollama rm "$DROP" >/dev/null 2>&1; then
  fail "qwen3_remove_failed"
fi

inventory >"$TMP/after.json" 2>/dev/null || fail "ollama_api_unreachable_after_cleanup"
python3 - "$TMP/after.json" "$KEEP1" "$KEEP2" "$DROP" <<'PY' || fail "postcleanup_inventory_invalid"
import json,sys
obj=json.load(open(sys.argv[1])); names={str(x.get('name') or x.get('model') or '') for x in obj.get('models',[])}
keep={sys.argv[2],sys.argv[3]}; drop=sys.argv[4]
raise SystemExit(0 if keep <= names and drop not in names else 1)
PY

FREE_AFTER="$(df -PB1 / | awk 'NR==2 {print $4}')"
REPORT="$(python3 - "$FREE_BEFORE" "$FREE_AFTER" <<'PY'
import sys
b,a=map(int,sys.argv[1:])
print('<!-- local-llm-cleanup-qwen3-v1 -->')
print('### Local LLM cleanup')
print('- status: PASS')
print('- removed_model: `qwen3:1.7b`')
print('- retained_model: `qwen2.5:1.5b`')
print('- retained_embedding_model: `nomic-embed-text:latest`')
print(f'- disk_free_before_gib: `{b/1024**3:.2f}`')
print(f'- disk_free_after_gib: `{a/1024**3:.2f}`')
print(f'- disk_recovered_gib: `{max(0,a-b)/1024**3:.2f}`')
print('- production_routing_changed: false')
print('- registry_changed: false')
print('- cloud_model_calls: zero')
PY
)"
post_report "$REPORT"
echo "ISSUE=https://github.com/alanua/Skeleton/issues/$ISSUE"
echo "RESULT=LOCAL_LLM_CLEANUP_PUBLISHED"
EOF
