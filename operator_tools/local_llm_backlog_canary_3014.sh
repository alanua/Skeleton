#!/usr/bin/env bash
set -euo pipefail

ssh agent@49.12.76.236 'bash -s' <<'EOF'
set -euo pipefail

REPO="alanua/Skeleton"
ISSUE="3014"
MARKER="<!-- local-llm-backlog-canary-v1 -->"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

post_report() {
  local body="$1"
  local cid
  cid="$(gh api "repos/$REPO/issues/$ISSUE/comments" --paginate --jq ".[] | select(.body | contains(\"$MARKER\")) | .id" 2>/dev/null | tail -n1 || true)"
  if [ -n "$cid" ]; then
    gh api --method PATCH "repos/$REPO/issues/comments/$cid" -f body="$body" >/dev/null
  else
    gh api --method POST "repos/$REPO/issues/$ISSUE/comments" -f body="$body" >/dev/null
  fi
}

if ! command -v python3 >/dev/null 2>&1 || ! command -v gh >/dev/null 2>&1; then
  post_report "$MARKER
### Local LLM backlog canary
- status: BLOCKED
- reason: required_local_tool_missing
- side_effects: zero"
  echo "RESULT=LOCAL_LLM_CANARY_PUBLISHED"
  exit 0
fi

python3 - <<'PY' >"$TMP/models.json" 2>"$TMP/ollama.err" || true
import json, urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as r:
        print(r.read().decode())
except Exception as e:
    raise SystemExit(1)
PY

if [ ! -s "$TMP/models.json" ]; then
  post_report "$MARKER
### Local LLM backlog canary
- status: BLOCKED
- local_runtime: unavailable
- reason: ollama_api_unreachable
- side_effects: zero"
  echo "RESULT=LOCAL_LLM_CANARY_PUBLISHED"
  exit 0
fi

MODEL="$(python3 - "$TMP/models.json" <<'PY'
import json,sys
obj=json.load(open(sys.argv[1]))
names=[str(x.get('name') or x.get('model') or '') for x in obj.get('models',[]) if (x.get('name') or x.get('model'))]
prefs=('qwen3','qwen2.5','gemma3','llama3.2','phi3','mistral','tinyllama')
for p in prefs:
    for n in names:
        if p in n.lower():
            print(n); raise SystemExit
if names: print(names[0])
PY
)"

if [ -z "$MODEL" ]; then
  post_report "$MARKER
### Local LLM backlog canary
- status: BLOCKED
- local_runtime: available
- reason: no_installed_model
- side_effects: zero"
  echo "RESULT=LOCAL_LLM_CANARY_PUBLISHED"
  exit 0
fi

ISSUES=(2343 2431 2432 2433 2435 2436 2927 2928 2930 2931)
: >"$TMP/issues.jsonl"
for n in "${ISSUES[@]}"; do
  gh issue view "$n" -R "$REPO" --json number,title,state,labels,body \
    | python3 -c 'import json,sys; x=json.load(sys.stdin); print(json.dumps({"number":x["number"],"title":x["title"],"state":x["state"],"labels":[i["name"] for i in x.get("labels",[])],"summary":(x.get("body") or "")[:900]}, ensure_ascii=False))' \
    >>"$TMP/issues.jsonl"
done

START="$(date +%s)"
python3 - "$MODEL" "$TMP/issues.jsonl" >"$TMP/result.json" <<'PY'
import json, sys, urllib.request
model=sys.argv[1]
items=[json.loads(line) for line in open(sys.argv[2], encoding='utf-8') if line.strip()]
prompt='''You are a bounded backlog classifier. Use ONLY the supplied public GitHub metadata. Do not propose edits or actions outside classification. For each issue choose exactly one class: ACTIVE, WAIT_DEPENDENCY, SUPERSEDED, NEEDS_REVIEW, SAFE_NO_MODEL. Return strict JSON object with key "items", each item: {"issue": integer, "class": one allowed class, "reason": <= 18 words}. Do not omit issues.\n\nINPUT:\n'''+json.dumps(items,ensure_ascii=False)
payload=json.dumps({"model":model,"prompt":prompt,"stream":False,"format":"json","options":{"temperature":0}}).encode()
req=urllib.request.Request('http://127.0.0.1:11434/api/generate', data=payload, headers={'Content-Type':'application/json'})
with urllib.request.urlopen(req, timeout=180) as r:
    outer=json.loads(r.read().decode())
text=str(outer.get('response') or '')
try:
    parsed=json.loads(text)
except Exception:
    parsed={"parse_error":True,"raw":text[:4000]}
print(json.dumps(parsed,ensure_ascii=False))
PY
END="$(date +%s)"
ELAPSED="$((END-START))"

REPORT="$(python3 - "$MODEL" "$ELAPSED" "$TMP/result.json" <<'PY'
import json,sys
model=sys.argv[1]; elapsed=sys.argv[2]
obj=json.load(open(sys.argv[3],encoding='utf-8'))
allowed={'ACTIVE','WAIT_DEPENDENCY','SUPERSEDED','NEEDS_REVIEW','SAFE_NO_MODEL'}
items=obj.get('items') if isinstance(obj,dict) else None
valid=isinstance(items,list) and len(items)==10
rows=[]
if valid:
    seen=set()
    for it in items:
        try:
            n=int(it.get('issue'))
        except Exception:
            valid=False; break
        c=str(it.get('class') or '')
        r=' '.join(str(it.get('reason') or '').split())[:180]
        if c not in allowed or n in seen:
            valid=False; break
        seen.add(n)
        rows.append(f'- #{n}: `{c}` — {r}')
marker='<!-- local-llm-backlog-canary-v1 -->'
print(marker)
print('### Local LLM backlog canary')
print(f'- status: {"PASS" if valid else "BLOCKED"}')
print('- local_runtime: available')
print(f'- local_model: `{model}`')
print(f'- elapsed_seconds: `{elapsed}`')
print('- mutation_authority: none')
print('- cloud_model_calls: zero')
print('- side_effects: zero')
if valid:
    print('\n#### Advisory classification')
    print('\n'.join(rows))
else:
    print('- reason: model_output_failed_strict_contract')
print('\nInterpretation: advisory reasoning evidence only; no Runner state or routing was changed.')
PY
)"

post_report "$REPORT"
echo "ISSUE=https://github.com/alanua/Skeleton/issues/3014"
echo "RESULT=LOCAL_LLM_CANARY_PUBLISHED"
EOF
