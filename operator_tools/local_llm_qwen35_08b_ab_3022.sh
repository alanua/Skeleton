#!/usr/bin/env bash
set -euo pipefail

: "${SKELETON_TARGET:?Set SKELETON_TARGET to the SSH target for the local Ollama host}"

ssh -o BatchMode=yes "$SKELETON_TARGET" 'bash -s' <<'EOF'
set -euo pipefail

REPO="alanua/Skeleton"
ISSUE="3022"
MARKER="<!-- local-llm-qwen35-08b-ab-v1 -->"
BASELINE="qwen2.5:1.5b"
REJECTED="qwen3:1.7b"
CANDIDATE="qwen3.5:0.8b"
EMBED="nomic-embed-text:latest"
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

fail_report() {
  local reason="$1"
  post_report "$MARKER
### Local LLM qwen3.5:0.8b A/B
- status: BLOCKED
- reason: $reason
- production_routing_changed: false
- registry_changed: false
- cloud_model_calls: zero"
  echo "ISSUE=https://github.com/alanua/Skeleton/issues/$ISSUE"
  echo "RESULT=LOCAL_LLM_QWEN35_AB_PUBLISHED"
  exit 0
}

for cmd in gh python3 ollama; do
  command -v "$cmd" >/dev/null 2>&1 || fail_report "required_local_tool_missing"
done

fetch_models() {
  python3 - <<'PY'
import urllib.request
with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=5) as r:
    print(r.read().decode())
PY
}

model_present() {
  local file="$1" model="$2"
  python3 - "$file" "$model" <<'PY'
import json,sys
obj=json.load(open(sys.argv[1]))
want=sys.argv[2]
names={str(x.get('name') or x.get('model') or '') for x in obj.get('models',[])}
raise SystemExit(0 if want in names else 1)
PY
}

fetch_models >"$TMP/models_before.json" 2>/dev/null || fail_report "ollama_api_unreachable"
model_present "$TMP/models_before.json" "$BASELINE" || fail_report "baseline_model_missing"
model_present "$TMP/models_before.json" "$EMBED" || fail_report "embedding_model_missing"

FREE_BEFORE="$(df -PB1 / | awk 'NR==2 {print $4}')"
CLEANUP_PERFORMED=false
if model_present "$TMP/models_before.json" "$REJECTED"; then
  ollama rm "$REJECTED" >/dev/null 2>&1 || fail_report "rejected_model_cleanup_failed"
  CLEANUP_PERFORMED=true
fi

fetch_models >"$TMP/models_after_cleanup.json" 2>/dev/null || fail_report "ollama_api_unreachable_after_cleanup"
if model_present "$TMP/models_after_cleanup.json" "$REJECTED"; then
  fail_report "rejected_model_still_present"
fi
model_present "$TMP/models_after_cleanup.json" "$BASELINE" || fail_report "baseline_missing_after_cleanup"
model_present "$TMP/models_after_cleanup.json" "$EMBED" || fail_report "embedding_missing_after_cleanup"
FREE_AFTER_CLEANUP="$(df -PB1 / | awk 'NR==2 {print $4}')"

MIN_BEFORE_PULL=$((2750*1024*1024))
[ "${FREE_AFTER_CLEANUP:-0}" -ge "$MIN_BEFORE_PULL" ] || fail_report "insufficient_free_disk_after_cleanup"

if ! model_present "$TMP/models_after_cleanup.json" "$CANDIDATE"; then
  timeout 600 ollama pull "$CANDIDATE" >/dev/null 2>&1 || fail_report "candidate_pull_failed"
fi

fetch_models >"$TMP/models_after_pull.json" 2>/dev/null || fail_report "ollama_api_unreachable_after_pull"
model_present "$TMP/models_after_pull.json" "$BASELINE" || fail_report "baseline_missing_after_pull"
model_present "$TMP/models_after_pull.json" "$EMBED" || fail_report "embedding_missing_after_pull"
model_present "$TMP/models_after_pull.json" "$CANDIDATE" || fail_report "candidate_missing_after_pull"
FREE_AFTER_PULL="$(df -PB1 / | awk 'NR==2 {print $4}')"
MIN_AFTER_PULL=$((1536*1024*1024))
[ "${FREE_AFTER_PULL:-0}" -ge "$MIN_AFTER_PULL" ] || fail_report "low_disk_after_candidate_pull"

ISSUES=(2431 2432 2927 2999 3016 3022)
: >"$TMP/items.jsonl"
for n in "${ISSUES[@]}"; do
  gh issue view "$n" -R "$REPO" --json number,title,state,labels \
    | python3 -c 'import json,sys; x=json.load(sys.stdin); print(json.dumps({"number":x["number"],"title":x["title"],"state":x["state"],"labels":[i["name"] for i in x.get("labels",[])]}, ensure_ascii=False))' \
    >>"$TMP/items.jsonl"
done

python3 - "$TMP/items.jsonl" >"$TMP/reference.json" <<'PY'
import json,sys
out={}
for line in open(sys.argv[1],encoding='utf-8'):
    x=json.loads(line)
    labels=set(x.get('labels',[]))
    state=str(x.get('state') or '').upper()
    if state == 'CLOSED':
        c='SUPERSEDED'
    elif 'runner:waiting-dependency' in labels:
        c='WAIT_DEPENDENCY'
    elif 'runner:blocked' in labels:
        c='NEEDS_REVIEW'
    else:
        c='ACTIVE'
    out[int(x['number'])]=c
print(json.dumps(out,sort_keys=True))
PY

run_model() {
  local model="$1" thinkmode="$2" outfile="$3" timefile="$4"
  local start end
  start="$(date +%s)"
  : >"$outfile"
  while IFS= read -r item; do
    python3 - "$model" "$thinkmode" "$item" >>"$outfile" <<'PY'
import json,re,sys,urllib.request
model,thinkmode,item=sys.argv[1],sys.argv[2],json.loads(sys.argv[3])
allowed=['ACTIVE','WAIT_DEPENDENCY','SUPERSEDED','NEEDS_REVIEW']
prompt='''Classify exactly one public GitHub issue. Return ONLY one token: ACTIVE, WAIT_DEPENDENCY, SUPERSEDED, or NEEDS_REVIEW. Apply these rules in order: if state is CLOSED => SUPERSEDED; else if labels contain runner:waiting-dependency => WAIT_DEPENDENCY; else if labels contain runner:blocked => NEEDS_REVIEW; otherwise => ACTIVE. Do not infer from title wording.\nITEM:\n'''+json.dumps(item,ensure_ascii=False)
payload={'model':model,'prompt':prompt,'stream':False,'keep_alive':0,'options':{'temperature':0,'num_predict':32,'num_ctx':2048}}
if thinkmode == 'off':
    payload['think']=False
req=urllib.request.Request('http://127.0.0.1:11434/api/generate',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
try:
    with urllib.request.urlopen(req,timeout=180) as r:
        obj=json.loads(r.read().decode())
    text=str(obj.get('response') or '').upper()
except Exception:
    text=''
found='UNPARSED'
for c in allowed:
    if re.search(r'\b'+re.escape(c)+r'\b',text):
        found=c
        break
print(json.dumps({'issue':int(item['number']),'class':found}))
PY
  done <"$TMP/items.jsonl"
  end="$(date +%s)"
  echo "$((end-start))" >"$timefile"
}

run_model "$BASELINE" legacy "$TMP/baseline.jsonl" "$TMP/baseline.time"
run_model "$CANDIDATE" off "$TMP/candidate.jsonl" "$TMP/candidate.time"

python3 - "$TMP/reference.json" "$TMP/baseline.jsonl" "$TMP/candidate.jsonl" "$TMP/baseline.time" "$TMP/candidate.time" "$FREE_BEFORE" "$FREE_AFTER_CLEANUP" "$FREE_AFTER_PULL" "$CLEANUP_PERFORMED" >"$TMP/report.md" <<'PY'
import json,sys
refp,bp,cp,bt,ct,fb,fc,fp,cleanup=sys.argv[1:]
ref={int(k):v for k,v in json.load(open(refp)).items()}
def load(path):
    out={}
    for line in open(path):
        x=json.loads(line); out[int(x['issue'])]=x['class']
    return out
b=load(bp); c=load(cp)
def score(m): return sum(1 for n,v in ref.items() if m.get(n)==v)
def gib(v): return f'{int(v)/1024**3:.2f}'
print('<!-- local-llm-qwen35-08b-ab-v1 -->')
print('### Local LLM qwen3.5:0.8b A/B')
print('- status: PASS')
print('- baseline_model: `qwen2.5:1.5b`')
print('- candidate_model: `qwen3.5:0.8b`')
print('- candidate_thinking: `disabled`')
print(f'- cleanup_rejected_qwen3_1_7b: `{cleanup}`')
print(f'- baseline_score: `{score(b)}/{len(ref)}`')
print(f'- candidate_score: `{score(c)}/{len(ref)}`')
print(f'- baseline_elapsed_seconds: `{open(bt).read().strip()}`')
print(f'- candidate_elapsed_seconds: `{open(ct).read().strip()}`')
print(f'- disk_free_before_gib: `{gib(fb)}`')
print(f'- disk_free_after_cleanup_gib: `{gib(fc)}`')
print(f'- disk_free_after_pull_gib: `{gib(fp)}`')
print('- baseline_preserved: true')
print('- embedding_model_preserved: true')
print('- production_routing_changed: false')
print('- registry_changed: false')
print('- cloud_model_calls: zero')
print('\n#### Reference / results')
for n in sorted(ref):
    print(f'- #{n}: reference=`{ref[n]}` | qwen2.5=`{b.get(n,"UNPARSED")}` | qwen3.5=`{c.get(n,"UNPARSED")}`')
print('\nDecision remains external; no production promotion or baseline deletion was performed.')
PY

post_report "$(cat "$TMP/report.md")"
echo "ISSUE=https://github.com/alanua/Skeleton/issues/$ISSUE"
echo "RESULT=LOCAL_LLM_QWEN35_AB_PUBLISHED"
EOF
