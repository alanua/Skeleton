#!/usr/bin/env bash
set -euo pipefail

ssh agent@49.12.76.236 'bash -s' <<'EOF'
set -euo pipefail

REPO="alanua/Skeleton"
ISSUE="3019"
MARKER="<!-- local-llm-ab-qwen3-1.7b-v1 -->"
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

fail_report() {
  local reason="$1"
  post_report "$MARKER
### Local LLM A/B canary
- status: BLOCKED
- reason: $reason
- production_routing_changed: false
- cloud_model_calls: zero"
  echo "ISSUE=https://github.com/alanua/Skeleton/issues/$ISSUE"
  echo "RESULT=LOCAL_LLM_AB_PUBLISHED"
  exit 0
}

for cmd in gh python3 ollama; do
  command -v "$cmd" >/dev/null 2>&1 || fail_report "required_local_tool_missing"
done

python3 - <<'PY' >"$TMP/models_before.json" 2>/dev/null || true
import urllib.request
with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=3) as r:
    print(r.read().decode())
PY
[ -s "$TMP/models_before.json" ] || fail_report "ollama_api_unreachable"

python3 - "$TMP/models_before.json" "$OLD_MODEL" <<'PY' || fail_report "baseline_model_missing"
import json,sys
obj=json.load(open(sys.argv[1]))
want=sys.argv[2]
names={str(x.get('name') or x.get('model') or '') for x in obj.get('models',[])}
raise SystemExit(0 if want in names else 1)
PY

FREE_BEFORE="$(df -PB1 / | awk 'NR==2 {print $4}')"
MIN_BEFORE=$((3*1024*1024*1024))
[ "${FREE_BEFORE:-0}" -ge "$MIN_BEFORE" ] || fail_report "insufficient_free_disk_before_pull"

# Pull only the approved bounded candidate. Suppress provider/download transcript.
if ! timeout 600 ollama pull "$NEW_MODEL" >/dev/null 2>&1; then
  fail_report "qwen3_pull_failed"
fi

python3 - <<'PY' >"$TMP/models_after.json" 2>/dev/null || true
import urllib.request
with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=3) as r:
    print(r.read().decode())
PY
python3 - "$TMP/models_after.json" "$NEW_MODEL" <<'PY' || fail_report "qwen3_not_registered_after_pull"
import json,sys
obj=json.load(open(sys.argv[1]))
want=sys.argv[2]
names={str(x.get('name') or x.get('model') or '') for x in obj.get('models',[])}
raise SystemExit(0 if want in names else 1)
PY

FREE_AFTER_PULL="$(df -PB1 / | awk 'NR==2 {print $4}')"
MIN_AFTER=$((1536*1024*1024))
if [ "${FREE_AFTER_PULL:-0}" -lt "$MIN_AFTER" ]; then
  ollama rm "$NEW_MODEL" >/dev/null 2>&1 || true
  fail_report "rolled_back_low_disk_after_pull"
fi

ISSUES=(2343 2431 2432 2927 2999)
: >"$TMP/issues.txt"
for n in "${ISSUES[@]}"; do
  gh issue view "$n" -R "$REPO" --json number,title,state,labels,body \
    | python3 -c 'import json,sys; x=json.load(sys.stdin); labels=",".join(i["name"] for i in x.get("labels",[])); body=" ".join((x.get("body") or "").split())[:500]; print("#%s | state=%s | labels=%s | title=%s | summary=%s" % (x["number"],x["state"],labels,x["title"],body))' \
    >>"$TMP/issues.txt"
done

run_model() {
  local model="$1" out="$2" timefile="$3"
  local start end
  start="$(date +%s)"
  if ! python3 - "$model" "$TMP/issues.txt" >"$out" <<'PY'
import json,sys,urllib.request
model=sys.argv[1]
text=open(sys.argv[2],encoding='utf-8').read()
prompt='''Classify each GitHub issue using ONLY the supplied public metadata. Allowed classes: ACTIVE, WAIT_DEPENDENCY, SUPERSEDED, NEEDS_REVIEW, SAFE_NO_MODEL. Output one line per issue in this exact shape: #1234 | CLASS. Do not explain. Do not omit an issue. Labels and explicit task mode are stronger evidence than title wording.\n\n'''+text
payload=json.dumps({
  'model': model,
  'prompt': prompt,
  'stream': False,
  'keep_alive': 0,
  'options': {'temperature': 0, 'num_predict': 160, 'num_ctx': 4096}
}).encode()
req=urllib.request.Request('http://127.0.0.1:11434/api/generate',data=payload,headers={'Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=240) as r:
    obj=json.loads(r.read().decode())
print(str(obj.get('response') or ''))
PY
  then
    echo "-1" >"$timefile"
    return 1
  fi
  end="$(date +%s)"
  echo "$((end-start))" >"$timefile"
}

run_model "$OLD_MODEL" "$TMP/old.raw" "$TMP/old.time" || true
run_model "$NEW_MODEL" "$TMP/new.raw" "$TMP/new.time" || true

python3 - "$TMP/old.raw" "$TMP/new.raw" "$TMP/old.time" "$TMP/new.time" "$FREE_BEFORE" "$FREE_AFTER_PULL" >"$TMP/report.md" <<'PY'
import re,sys
oldp,newp,oldt,newt,fb,fa=sys.argv[1:]
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
def gib(x):
    try: return f'{int(x)/1024**3:.2f}'
    except: return 'unknown'
print('<!-- local-llm-ab-qwen3-1.7b-v1 -->')
print('### Local LLM A/B canary')
print(f'- status: {"PASS" if set(old)==expected and set(new)==expected else "PARTIAL"}')
print('- baseline_model: `qwen2.5:1.5b`')
print('- candidate_model: `qwen3:1.7b`')
print(f'- baseline_elapsed_seconds: `{open(oldt).read().strip() if __import__("os").path.exists(oldt) else "-1"}`')
print(f'- candidate_elapsed_seconds: `{open(newt).read().strip() if __import__("os").path.exists(newt) else "-1"}`')
print(f'- baseline_parsed_items: `{len(old)}/5`')
print(f'- candidate_parsed_items: `{len(new)}/5`')
print(f'- disk_free_before_gib: `{gib(fb)}`')
print(f'- disk_free_after_pull_gib: `{gib(fa)}`')
print('- production_routing_changed: false')
print('- registry_changed: false')
print('- cloud_model_calls: zero')
print('\n#### Baseline advisory mapping')
for n in sorted(old): print(f'- #{n}: `{old[n]}`')
print('\n#### Candidate advisory mapping')
for n in sorted(new): print(f'- #{n}: `{new[n]}`')
print('\nDecision remains external: no model is promoted by this canary.')
PY

post_report "$(cat "$TMP/report.md")"
echo "ISSUE=https://github.com/alanua/Skeleton/issues/$ISSUE"
echo "RESULT=LOCAL_LLM_AB_PUBLISHED"
EOF
