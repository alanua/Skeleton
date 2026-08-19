#!/usr/bin/env bash
set -euo pipefail
ssh agent@49.12.76.236 'bash -s' <<'EOF'
set -euo pipefail
REPO="alanua/Skeleton"
ISSUE="3015"
MARKER="<!-- local-llm-inventory-v1 -->"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
post_report() {
  local body="$1" cid
  cid="$(gh api "repos/$REPO/issues/$ISSUE/comments" --paginate --jq ".[] | select(.body | contains(\"$MARKER\")) | .id" 2>/dev/null | tail -n1 || true)"
  if [ -n "$cid" ]; then gh api --method PATCH "repos/$REPO/issues/comments/$cid" -f body="$body" >/dev/null; else gh api --method POST "repos/$REPO/issues/$ISSUE/comments" -f body="$body" >/dev/null; fi
}
python3 - <<'PY' >"$TMP/models.json" 2>/dev/null || true
import urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=5) as r:
        print(r.read().decode())
except Exception:
    raise SystemExit(1)
PY
if [ ! -s "$TMP/models.json" ]; then
  post_report "$MARKER
### Local Ollama inventory
- status: BLOCKED
- local_runtime: unavailable
- side_effects: zero"
  echo "RESULT=LOCAL_LLM_INVENTORY_PUBLISHED"
  exit 0
fi
REPORT="$(python3 - "$TMP/models.json" <<'PY'
import json,sys
obj=json.load(open(sys.argv[1]))
rows=[]
for m in obj.get('models',[]):
    name=str(m.get('name') or m.get('model') or '')
    if not name: continue
    size=int(m.get('size') or 0)
    rows.append((size,name))
rows.sort(reverse=True)
print('<!-- local-llm-inventory-v1 -->')
print('### Local Ollama inventory')
print('- status: PASS')
print('- local_runtime: available')
print(f'- installed_models_count: `{len(rows)}`')
print('- side_effects: zero')
print('- model_downloads: zero')
if rows:
    print('\n#### Installed models')
    for size,name in rows:
        gib=size/(1024**3) if size else 0
        print(f'- `{name}` — `{gib:.2f} GiB`')
    print(f'\n- largest_installed_model: `{rows[0][1]}`')
else:
    print('- reason: no_installed_models')
PY
)"
post_report "$REPORT"
echo "ISSUE=https://github.com/alanua/Skeleton/issues/3015"
echo "RESULT=LOCAL_LLM_INVENTORY_PUBLISHED"
EOF
