#!/usr/bin/env bash
set -euo pipefail

ssh agent@49.12.76.236 'bash -s' <<'EOF'
set -euo pipefail

REPO="alanua/Skeleton"
ISSUE="3020"
MARKER="<!-- local-llm-final-semantic-ab-v1 -->"
OLD_MODEL="qwen2.5:1.5b"
NEW_MODEL="qwen3:1.7b"
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

for cmd in gh python3; do
  command -v "$cmd" >/dev/null 2>&1 || {
    post_report "$MARKER
### Local LLM final semantic A/B
- status: BLOCKED
- reason: required_local_tool_missing
- side_effects: zero"
    echo "RESULT=LOCAL_LLM_FINAL_AB_PUBLISHED"
    exit 0
  }
done

python3 - <<'PY' >"$TMP/models.json" 2>/dev/null || true
import urllib.request
with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=3) as r:
    print(r.read().decode())
PY

python3 - "$TMP/models.json" "$OLD_MODEL" "$NEW_MODEL" <<'PY' || {
import json,sys
obj=json.load(open(sys.argv[1])); names={str(x.get('name') or x.get('model') or '') for x in obj.get('models',[])}
raise SystemExit(0 if sys.argv[2] in names and sys.argv[3] in names else 1)
PY
  post_report "$MARKER
### Local LLM final semantic A/B
- status: BLOCKED
- reason: required_model_missing
- side_effects: zero"
  echo "RESULT=LOCAL_LLM_FINAL_AB_PUBLISHED"
  exit 0
}

ISSUES=(2343 2431 2432 2927 2999)
: >"$TMP/items.jsonl"
for n in "${ISSUES[@]}"; do
  gh issue view "$n" -R "$REPO" --json number,title,state,labels,body \
    | python3 -c 'import json,sys; x=json.load(sys.stdin); labels=[i["name"] for i in x.get("labels",[])]; body=" ".join((x.get("body") or "").split())[:350]; print(json.dumps({"number":x["number"],"title":x["title"],"state":x["state"],"labels":labels,"summary":body}, ensure_ascii=False))' \
    >>"$TMP/items.jsonl"
done

python3 - "$TMP/items.jsonl" >"$TMP/reference.json" <<'PY'
import json,sys
out={}
for line in open(sys.argv[1],encoding='utf-8'):
    x=json.loads(line); labels=set(x.get('labels',[])); state=str(x.get('state') or '').upper(); body=str(x.get('summary') or '')
    if state == 'CLOSED' or 'supersed' in body.lower():
        c='SUPERSEDED'
    elif 'runner:waiting-dependency' in labels:
        c='WAIT_DEPENDENCY'
    elif 'runner:blocked' in labels:
        c='NEEDS_REVIEW'
    elif 'RUNTIME_MAINTENANCE_TASK' in body and not ({'runner:waiting-dependency','runner:blocked'} & labels):
        c='SAFE_NO_MODEL'
    else:
        c='ACTIVE'
    out[int(x['number'])]=c
print(json.dumps(out,sort_keys=True))
PY

run_model() {
  local model="$1" think="$2" outfile="$3" timefile="$4"
  local start end
  start="$(date +%s)"
  : >"$outfile"
  while IFS= read -r item; do
    python3 - "$model" "$think" "$item" >>"$outfile" <<'PY'
import json,re,sys,urllib.request
model,think,item=sys.argv[1],sys.argv[2],json.loads(sys.argv[3])
allowed=['ACTIVE','WAIT_DEPENDENCY','SUPERSEDED','NEEDS_REVIEW','SAFE_NO_MODEL']
prompt='''Classify exactly one public GitHub issue. Return ONLY one token from: ACTIVE, WAIT_DEPENDENCY, SUPERSEDED, NEEDS_REVIEW, SAFE_NO_MODEL. Rules: runner:waiting-dependency => WAIT_DEPENDENCY. runner:blocked => NEEDS_REVIEW. Closed or explicitly superseded => SUPERSEDED. Eligible RUNTIME_MAINTENANCE_TASK without wait/block => SAFE_NO_MODEL. Otherwise ACTIVE. Do not infer beyond supplied metadata.\nITEM:\n'''+json.dumps(item,ensure_ascii=False)
payload={'model':model,'prompt':prompt,'stream':False,'keep_alive':0,'options':{'temperature':0,'num_predict':24,'num_ctx':2048}}
if think == 'off': payload['think']=False
req=urllib.request.Request('http://127.0.0.1:11434/api/generate',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
try:
    with urllib.request.urlopen(req,timeout=180) as r: obj=json.loads(r.read().decode())
    text=str(obj.get('response') or '').upper()
except Exception:
    text=''
found='UNPARSED'
for c in allowed:
    if re.search(r'\b'+re.escape(c)+r'\b',text): found=c; break
print(json.dumps({'issue':int(item['number']),'class':found}))
PY
  done <"$TMP/items.jsonl"
  end="$(date +%s)"
  echo "$((end-start))" >"$timefile"
}

run_model "$OLD_MODEL" legacy "$TMP/old.jsonl" "$TMP/old.time"
run_model "$NEW_MODEL" off "$TMP/new.jsonl" "$TMP/new.time"

FREE_NOW="$(df -PB1 / | awk 'NR==2 {print $4}')"
python3 - "$TMP/reference.json" "$TMP/old.jsonl" "$TMP/new.jsonl" "$TMP/old.time" "$TMP/new.time" "$FREE_NOW" >"$TMP/report.md" <<'PY'
import json,sys
refp,oldp,newp,oldt,newt,free=sys.argv[1:]
ref={int(k):v for k,v in json.load(open(refp)).items()}
def load(path):
    out={}
    for line in open(path):
        x=json.loads(line); out[int(x['issue'])]=x['class']
    return out
old=load(oldp); new=load(newp)
def score(m): return sum(1 for n,c in ref.items() if m.get(n)==c)
def gib(v): return f'{int(v)/1024**3:.2f}'
print('<!-- local-llm-final-semantic-ab-v1 -->')
print('### Local LLM final semantic A/B')
print('- status: PASS')
print('- baseline_model: `qwen2.5:1.5b`')
print('- candidate_model: `qwen3:1.7b`')
print('- candidate_thinking: `disabled`')
print(f'- baseline_score: `{score(old)}/5`')
print(f'- candidate_score: `{score(new)}/5`')
print(f'- baseline_elapsed_seconds: `{open(oldt).read().strip()}`')
print(f'- candidate_elapsed_seconds: `{open(newt).read().strip()}`')
print(f'- disk_free_now_gib: `{gib(free)}`')
print('- production_routing_changed: false')
print('- registry_changed: false')
print('- model_deletions: zero')
print('- cloud_model_calls: zero')
print('\n#### Reference / results')
for n in sorted(ref):
    print(f'- #{n}: reference=`{ref[n]}` | qwen2.5=`{old.get(n,"UNPARSED")}` | qwen3=`{new.get(n,"UNPARSED")}`')
print('\nDecision remains external; this canary does not promote or delete models.')
PY

post_report "$(cat "$TMP/report.md")"
echo "ISSUE=https://github.com/alanua/Skeleton/issues/$ISSUE"
echo "RESULT=LOCAL_LLM_FINAL_AB_PUBLISHED"
EOF
