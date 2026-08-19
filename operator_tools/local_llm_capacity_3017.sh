#!/usr/bin/env bash
set -euo pipefail

ssh agent@49.12.76.236 'bash -s' <<'EOF'
set -euo pipefail
REPO="alanua/Skeleton"
ISSUE="3017"
MARKER="<!-- local-llm-capacity-v1 -->"

post_report() {
  local body="$1" cid
  cid="$(gh api "repos/$REPO/issues/$ISSUE/comments" --paginate --jq ".[] | select(.body | contains(\"$MARKER\")) | .id" 2>/dev/null | tail -n1 || true)"
  if [ -n "$cid" ]; then
    gh api --method PATCH "repos/$REPO/issues/comments/$cid" -f body="$body" >/dev/null
  else
    gh api --method POST "repos/$REPO/issues/$ISSUE/comments" -f body="$body" >/dev/null
  fi
}

if ! command -v python3 >/dev/null 2>&1 || ! command -v gh >/dev/null 2>&1; then
  post_report "$MARKER
### Local LLM host capacity
- status: BLOCKED
- reason: required_local_tool_missing
- side_effects: zero"
  echo "RESULT=LOCAL_LLM_CAPACITY_PUBLISHED"
  exit 0
fi

REPORT="$(python3 - <<'PY'
import os, platform, shutil, urllib.request

def gib(kib):
    return round(kib / 1024 / 1024, 2)

mem={}
try:
    for line in open('/proc/meminfo', encoding='utf-8'):
        if ':' not in line: continue
        k,v=line.split(':',1)
        parts=v.strip().split()
        if parts and parts[0].isdigit(): mem[k]=int(parts[0])
except Exception:
    pass

cpu=os.cpu_count() or 0
arch=platform.machine() or 'unknown'
ram_total=gib(mem.get('MemTotal',0))
ram_avail=gib(mem.get('MemAvailable',0))
swap_total=gib(mem.get('SwapTotal',0))
swap_free=gib(mem.get('SwapFree',0))

candidates=['/usr/share/ollama/.ollama/models','/var/lib/ollama','/root/.ollama/models',os.path.expanduser('~/.ollama/models'),'/']
probe='/'
for p in candidates:
    if os.path.exists(p):
        probe=p; break
try:
    du=shutil.disk_usage(probe)
    disk_free=round(du.free/1024/1024/1024,2)
    disk_total=round(du.total/1024/1024/1024,2)
except Exception:
    disk_free=disk_total=0

ollama='unavailable'
try:
    with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=3) as r:
        if r.status == 200: ollama='available'
except Exception:
    pass

print('<!-- local-llm-capacity-v1 -->')
print('### Local LLM host capacity')
print('- status: PASS')
print(f'- cpu_logical: `{cpu}`')
print(f'- architecture: `{arch}`')
print(f'- ram_total_gib: `{ram_total}`')
print(f'- ram_available_gib: `{ram_avail}`')
print(f'- swap_total_gib: `{swap_total}`')
print(f'- swap_free_gib: `{swap_free}`')
print(f'- ollama_filesystem_total_gib: `{disk_total}`')
print(f'- ollama_filesystem_free_gib: `{disk_free}`')
print(f'- ollama_runtime: {ollama}')
print('- side_effects: zero')
print('- model_downloads: zero')
PY
)"

post_report "$REPORT"
echo "ISSUE=https://github.com/alanua/Skeleton/issues/3017"
echo "RESULT=LOCAL_LLM_CAPACITY_PUBLISHED"
EOF
