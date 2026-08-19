#!/usr/bin/env bash
set -euo pipefail

ssh agent@49.12.76.236 'bash -s' <<'EOF'
set -euo pipefail
REPO="alanua/Skeleton"
ISSUE="3014"
MARKER="<!-- local-llm-backlog-canary-v2 -->"
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

python3 - <<'PY' >"$TMP/models.json" 2>/dev/null || true
import urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=3) as r:
        print(r.read().decode())
except Exception:
    raise SystemExit(1)
PY

if [ ! -s "$TMP/models.json" ]; then
  post_report "$MARKER
### Local LLM backlog canary v2
- status: BLOCKED
- reason: ollama_api_unreachable
- side_effects: zero"
  echo "RESULT=LOCAL_LLM_CANARY_V2_PUBLISHED"
  exit 0
fi

MODEL="$(python3 - "$TMP/models.json" <<'PY'
import json,sys
obj=json.load(open(sys.argv[1])); names=[str(x.get('name') or x.get('model') or '') for x in obj.get('models',[]) if x.get('name') or x.get('model')]
for p in ('qwen2.5','qwen3','gemma3','llama3.2','phi3','mistral','tinyllama'):
    for n in names:
        if p in n.lower(): print(n); raise SystemExit
if names: print(names[0])
PY
)"

if [ -z "$MODEL" ]; then
  post_report "$MARKER
### Local LLM backlog canary v2
- status: BLOCKED
- reason: no_installed_model
- side_effects: zero"
  echo "RESULT=LOCAL_LLM_CANARY_V2_PUBLISHED"
  exit 0
fi

ISSUES=(2343 2431 2432 2927 2999)
: >"$TMP/issues.txt"
for n in "${ISSUES[@]}"; do
  gh issue view "$n" -R "$REPO" --json number,title,state,labels,body \
    | python3 -c 'import json,sys; x=json.load(sys.stdin); labels=",".join(i["name"] for i in x.get("labels",[])); body=" ".join((x.get("body") or "").split())[:500]; print(f"#{x[\"number\"]} | state={x[\"state\"]} | labels={labels} | title={x[\"title\"]} | summary={body}")' \
    >>"$TMP/issues.txt"
done

START="$(date +%s)"
python3 - "$MODEL" "$TMP/issues.txt" >"$TMP/raw.txt" <<'PY'
import json,sys,urllib.request
model=sys.argv[1]; text=open(sys.argv[2],encoding='utf-8').read()
prompt='''Classify each GitHub issue using ONLY its supplied public metadata. Allowed classes: ACTIVE, WAIT_DEPENDENCY, SUPERSEDED, NEEDS_REVIEW, SAFE_NO_MODEL. Output one line per issue in this exact simple shape: #1234 | CLASS. Do not explain. Do not omit an issue.\n\n'''+text
payload=json.dumps({'model':model,'prompt':prompt,'stream':False,'options':{'temperature':0,'num_predict':160}}).encode()
req=urllib.request.Request('http://127.0.0.1:11434/api/generate',data=payload,headers={'Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=180) as r:
    out=json.loads(r.read().decode())
print(str(out.get('response') or ''))
PY
END="$(date +%s)"; ELAPSED="$((END-START))"

REPORT="$(python3 - "$MODEL" "$ELAPSED" "$TMP/raw.txt" <<'PY'
import re,sys
model,elapsed,path=sys.argv[1:]
raw=open(path,encoding='utf-8',errors='replace').read()
allowed={'ACTIVE','WAIT_DEPENDENCY','SUPERSEDED','NEEDS_REVIEW','SAFE_NO_MODEL'}
expected={2343,2431,2432,2927,2999}
found={}
for n,c in re.findall(r'#?(\d{4})\s*\|\s*(ACTIVE|WAIT_DEPENDENCY|SUPERSEDED|NEEDS_REVIEW|SAFE_NO_MODEL)',raw.upper()):
    n=int(n)
    if n in expected and c in allowed: found[n]=c
valid=set(found)==expected
print('<!-- local-llm-backlog-canary-v2 -->')
print('### Local LLM backlog canary v2')
print(f'- status: {"PASS" if valid else "BLOCKED"}')
print('- local_runtime: available')
print(f'- local_model: `{model}`')
print(f'- elapsed_seconds: `{elapsed}`')
print(f'- parsed_items: `{len(found)}/5`')
print('- mutation_authority: none')
print('- cloud_model_calls: zero')
print('- side_effects: zero')
if found:
    print('\n#### Advisory classification')
    for n in sorted(found): print(f'- #{n}: `{found[n]}`')
if not valid: print('- reason: incomplete_simple_contract')
print('\nInterpretation: advisory only; no Runner state/routing changed.')
PY
)"
post_report "$REPORT"
echo "ISSUE=https://github.com/alanua/Skeleton/issues/3014"
echo "RESULT=LOCAL_LLM_CANARY_V2_PUBLISHED"
EOF
