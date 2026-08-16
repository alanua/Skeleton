#!/usr/bin/env bash
set -euo pipefail

HOST="agent@49.12.76.236"

ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$HOST" 'bash -s' <<'REMOTE'
set -euo pipefail
REPO_FULL="alanua/Skeleton"
ISSUE="2816"
LAUNCHER="/usr/local/bin/skeleton-home-edge-exec-mcp"
BODY="$(mktemp)"
OUT="$(mktemp)"
cleanup(){ rm -f "$BODY" "$OUT"; }
trap cleanup EXIT

if [ "$(hostname)" != "hetzner-agent-runner-1" ] || [ "$(id -un)" != "agent" ]; then
  echo 'RESULT=BLOCKED:runner_identity'; exit 0
fi
if [ ! -x "$LAUNCHER" ]; then
  echo 'RESULT=BLOCKED:mcp_runtime_unavailable'; exit 0
fi

python3 - "$LAUNCHER" >"$OUT" <<'PY'
from __future__ import annotations
import json,re,select,subprocess,sys,time
launcher=sys.argv[1]
NODE_SCRIPT=r'''set -euo pipefail
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"
PID="$(systemctl --user show skeleton-cast.service -p MainPID --value 2>/dev/null || true)"
case "$PID" in ''|*[!0-9]*) PID=0;; esac
BASE=""
if [ "$PID" -gt 0 ] 2>/dev/null; then
  CWD="$(readlink -f "/proc/$PID/cwd" 2>/dev/null || true)"
  [ -n "$CWD" ] && [ -d "$CWD" ] && BASE="$CWD"
fi
[ -z "$BASE" ] && [ -d "$HOME/.local/lib/skeleton-cast" ] && BASE="$HOME/.local/lib/skeleton-cast"
[ -n "$BASE" ] && [ -f "$BASE/app.py" ] || { echo 'EXTRACT=NO_LIVE_APP'; exit 0; }
python3 - "$BASE/app.py" <<'PY_NODE'
from __future__ import annotations
import ast,io,re,sys,tokenize
from pathlib import Path
path=Path(sys.argv[1])
source=path.read_text(encoding='utf-8',errors='strict')
tree=ast.parse(source)
needle=re.compile(r'autoplay|auto_play|next_episode|episode_next|play_next|queue_next',re.I)
selected=[]
for node in ast.walk(tree):
    if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
        seg=ast.get_source_segment(source,node) or ''
        if needle.search(node.name) or needle.search(seg):
            selected.append(node)
# Add relevant module-level assignments.
for node in tree.body:
    if isinstance(node,(ast.Assign,ast.AnnAssign)):
        seg=ast.get_source_segment(source,node) or ''
        if needle.search(seg): selected.append(node)
# de-dup and sort; cap scope.
uniq={ (n.lineno,getattr(n,'end_lineno',n.lineno),type(n).__name__): n for n in selected }
items=sorted(uniq.values(), key=lambda n:(n.lineno,getattr(n,'end_lineno',n.lineno)))[:30]
print(f'EXTRACT_FUNCTION_COUNT={sum(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) for n in items)}')
print(f'EXTRACT_ITEM_COUNT={len(items)}')

def sanitize(text:str)->str:
    out=[]
    try:
        toks=tokenize.generate_tokens(io.StringIO(text).readline)
        for tok in toks:
            if tok.type==tokenize.STRING:
                tok=tokenize.TokenInfo(tok.type, repr('<redacted>'), tok.start,tok.end,tok.line)
            elif tok.type==tokenize.COMMENT:
                tok=tokenize.TokenInfo(tok.type,'',tok.start,tok.end,tok.line)
            out.append(tok)
        return tokenize.untokenize(out)
    except Exception:
        return re.sub(r"(['\"])(?:\\.|(?!\1).)*\1", "'<redacted>'", text)

for idx,node in enumerate(items,1):
    start=node.lineno; end=getattr(node,'end_lineno',start)
    if end-start>180: end=start+180
    lines=source.splitlines()[start-1:end]
    text='\n'.join(lines)
    safe=sanitize(text)
    name=getattr(node,'name',type(node).__name__)
    name=re.sub(r'[^A-Za-z0-9_.-]','_',name)[:80]
    print(f'ITEM_BEGIN={idx}|TYPE={type(node).__name__}|NAME={name}|LINE_START={start}|LINE_END={end}')
    for line in safe.splitlines():
        # reject absolute paths, URLs, IP-like literals even after token sanitizing.
        line=re.sub(r'/home/[^\s\'\"]+','<path>',line)
        line=re.sub(r'https?://\S+','<url>',line)
        line=re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b','<ip>',line)
        print('CODE='+line[:500])
    print(f'ITEM_END={idx}')
PY_NODE
'''

def send(p,x):
    p.stdin.write(json.dumps(x,sort_keys=True,separators=(',',':'))+'\n');p.stdin.flush()
def recv(p,t):
    r,_,_=select.select([p.stdout],[],[],t)
    if not r: raise RuntimeError('timeout')
    x=json.loads(p.stdout.readline())
    if not isinstance(x,dict): raise RuntimeError('invalid')
    return x
p=subprocess.Popen([launcher],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1)
try:
    send(p,{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'issue-2816-policy-extract','version':'1'}}})
    if 'error' in recv(p,12): raise RuntimeError('init')
    send(p,{'jsonrpc':'2.0','method':'notifications/initialized'})
    send(p,{'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'home_edge_exec','arguments':{
      'node_id':'home-edge-01','execution_lane':'read_only','run_as':'desktop-user','mode':'script','script_interpreter':'bash','script':NODE_SCRIPT,
      'timeout_seconds':90,'public':False,'idempotency_key':f'issue-2816-policy-extract-{int(time.time())}'}}})
    x=recv(p,120)
    if 'error' in x: raise RuntimeError('call')
    receipt=json.loads((x.get('result',{}).get('content') or [{}])[0].get('text') or '{}')
    if receipt.get('status')!='ok': raise RuntimeError('receipt')
    print('STATUS=OK')
    print('RECEIPT_HASH='+str(receipt.get('receipt_hash') or 'NONE'))
    text=str(receipt.get('stdout') or '')
    # final guard against sensitive patterns.
    text=re.sub(r'(?i)(password|passwd|secret|token|credential|authorization)\s*=\s*\S+',r'\1=<redacted>',text)
    text=re.sub(r'https?://\S+','<url>',text)
    text=re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b','<ip>',text)
    for line in text.splitlines()[:1200]: print(line[:600])
except Exception as exc:
    print('STATUS=BLOCKED')
    print('ERROR_CLASS='+type(exc).__name__.upper())
finally:
    if p.poll() is None:
        p.terminate()
        try:p.wait(timeout=2)
        except subprocess.TimeoutExpired:p.kill()
PY

STATUS="$(grep '^STATUS=' "$OUT" | tail -1 | cut -d= -f2 || true)"
{
 echo '### #2816 sanitized live autoplay policy extract'
 echo
 echo '```text'
 sed -n '1,1200p' "$OUT"
 echo '```'
} >"$BODY"
URL="$(gh issue comment "$ISSUE" --repo "$REPO_FULL" --body-file "$BODY" 2>/dev/null || true)"
if [ "$STATUS" = OK ]; then echo 'RESULT=POLICY_EXTRACT_READY'; else echo 'RESULT=BLOCKED:policy_extract'; fi
[ -n "$URL" ] && echo "RECEIPT_REF=$URL" || echo 'RECEIPT_REF=NOT_PUBLISHED'
REMOTE
rc=$?
echo 'RETURNED_TO_TERMUX=1'
echo "REMOTE_RC=$rc"
exit "$rc"
