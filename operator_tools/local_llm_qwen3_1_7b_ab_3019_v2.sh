#!/usr/bin/env bash
set -euo pipefail

ssh agent@49.12.76.236 'bash -s' <<'EOF'
set -euo pipefail

REPO="alanua/Skeleton"
ISSUE="3019"
MARKER="<!-- local-llm-ab-qwen3-1.7b-v2 -->"
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

for cmd in gh python3 ollama; do
  command -v "$cmd" >/dev/null 2>&1 || {
    post_report "$MARKER
### Local LLM A/B canary v2
- status: BLOCKED
- reason: required_local_tool_missing
- side_effects: zero"
    echo "RESULT=LOCAL_LLM_AB_V2_PUBLISHED"
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
### Local LLM A/B canary v2
- status: BLOCKED
- reason: required_model_missing
- side_effects: zero"
  echo "RESULT=LOCAL_LLM_AB_V2_PUBLISHED"
  exit 0
}

ISSUES=(2343 2431 2432 2927 2999)
: >"$TMP/issues.txt"
for n in "${ISSUES[@]}"; do
  gh issue view "$n" -R "$REPO" --json number,title,state,labels,body \
    | python3 -c 'import json,sys; x=json.load(sys.stdin); labels=",".join(i["name"] for i in x.get("labels",[])); body=" ".join((x.get("body") or "").split())[:500]; print("#%s | state=%s | labels=%s | title=%s | summary=%s" % (x["number"],x["state"],labels,x["title"],body))' \
    >>"$TMP/issues.txt"
done

run_model() {
  local model="$1" thinkmode="$2" out="$3" timefile="$4"
  local start end
  start="$(date +%s)"
  python3 - "$model" "$thinkmode" "$TMP/issues.txt" >"$out" <<'PY'
import json,sys,urllib.request
model=sys.argv[1]; thinkmode=sys.argv[2]; text=open(sys.argv[3],encoding='utf-8').read()
prompt='''Classify each GitHub issue using ONLY the supplied public metadata. Allowed classes: ACTIVE, WAIT_DEPENDENCY, SUPERSEDED, NEEDS_REVIEW, SAFE_NO_MODEL. Output exactly five lines and nothing else. Shape: #1234 | CLASS. Explicit runner:waiting-dependency means WAIT_DEPENDENCY. Closed/superseded means SUPERSEDED. RUNNER_TASK requiring code changes is not SAFE_NO_MODEL. RUNTIME_MAINTENANCE_TASK can be SAFE_NO_MODEL when not waiting.\n\n'''+text
payload={
  'model':model,
  'prompt':prompt,
  'stream':False,
  'keep_alive':0,
  'options':{'temperature':0,'num_predict':192,'num_ctx':4096}
}
if thinkmode == 'off':
    payload['think']=False
req=urllib.request.Request('http://127.0.0.1:11434/api/generate',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=240) as r:
    obj=json.loads(r.read().decode())
print(str(obj.get('response') or ''))
PY
  end="$(date +%s)"
  echo "$((end-start))" >"$timefile"
}

run_model "$OLD_MODEL" legacy "$TMP/old.raw" "$TMP/old.time" || true
run_model "$NEW_MODEL" off "$TMP/new.raw" "$TMP/new.time" || true

FREE_NOW="$(df -PB1 / | awk 'NR==2 {print $4}')"
python3 - "$TMP/old.raw" "$TMP/new.raw" "$TMP/old.time" "$TMP/new.time" "$FREE_NOW" >"$TMP/report.md" <<'PY'
import os,re,sys
oldp,newp,oldt,newt,free=sys.argv[1:]
expected={2343,2431,2432,2927,2999}
allowed={'ACTIVE','WAIT_DEPENDENCY','SUPERSEDED','NEEDS_REVIEW','SAFE_NO_MODEL'}
def parse(path):
    try: raw=open(path,encoding='utf-8',errors='replace').read()
    except Exception: return {}
    out={}
    for n,c in re.findall(r'#?(\d{4})\s*\|\s*(ACTIVE|WAIT_DEPENDENCY|SUPERSEDED|NEEDS_REVIEW|SAFE_NO_MODEL)',raw.upper()):
        n=int(n)
        if n in expected and c in allowed: out[n]=c
    return out
old=parse(oldp); new=parse(newp)
def t(path):
    return open(path).read().strip() if os.path.exists(path) else '-1'
def gib(v):
    try: return f'{int(v)/1024**3:.2f}'
    except: return 'unknown'
print('<!-- local-llm-ab-qwen3-1.7b-v2 -->')
print('### Local LLM A/B canary v2')
print(f'- status: {"PASS" if set(old)==expected and set(new)==expected else "PARTIAL"}')
print('- baseline_model: `qwen2.5:1.5b`')
print('- candidate_model: `qwen3:1.7b`')
print('- candidate_thinking: `disabled`')
print(f'- baseline_elapsed_seconds: `{t(oldt)}`')
print(f'- candidate_elapsed_seconds: `{t(newt)}`')
print(f'- baseline_parsed_items: `{len(old)}/5`')
print(f'- candidate_parsed_items: `{len(new)}/5`')
print(f'- disk_free_now_gib: `{gib(free)}`')
print('- production_routing_changed: false')
print('- registry_changed: false')
print('- cloud_model_calls: zero')
print('\n#### Baseline advisory mapping')
for n in sorted(old): print(f'- #{n}: `{old[n]}`')
print('\n#### Candidate advisory mapping')
for n in sorted(new): print(f'- #{n}: `{new[n]}`')
print('\nNo model promotion or deletion was performed.')
PY

post_report "$(cat "$TMP/report.md")"
echo "ISSUE=https://github.com/alanua/Skeleton/issues/$ISSUE"
echo "RESULT=LOCAL_LLM_AB_V2_PUBLISHED"
EOF
