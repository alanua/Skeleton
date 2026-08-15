#!/usr/bin/env bash
set -euo pipefail

HOST="agent@49.12.76.236"

ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$HOST" 'bash -s' <<'REMOTE'
set -euo pipefail

REPO_FULL="alanua/Skeleton"
ISSUE="2816"
LAUNCHER="/usr/local/bin/skeleton-home-edge-exec-mcp"
REPO_DIR="/home/agent/agent-dev/repos/Skeleton"
BODY="$(mktemp)"
OUT="$(mktemp)"
cleanup() { rm -f "$BODY" "$OUT"; }
trap cleanup EXIT

if [ "$(hostname)" != "hetzner-agent-runner-1" ] || [ "$(id -un)" != "agent" ]; then
  printf 'RESULT=BLOCKED:runner_identity\n'
  exit 0
fi
if [ ! -x "$LAUNCHER" ] || [ ! -d "$REPO_DIR/.git" ]; then
  printf 'RESULT=BLOCKED:mcp_runtime_unavailable\n'
  exit 0
fi

python3 - "$LAUNCHER" >"$OUT" <<'PY'
from __future__ import annotations

import json
import os
import re
import select
import subprocess
import sys
import time

launcher = sys.argv[1]

NODE_SCRIPT = r'''set -euo pipefail
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"
UNIT="skeleton-cast.service"
STATE="$(systemctl --user is-active "$UNIT" 2>/dev/null || true)"
case "$STATE" in active|inactive|failed|activating|deactivating) ;; *) STATE=unknown ;; esac
printf 'SERVICE_STATE=%s\n' "$STATE"
PID="$(systemctl --user show "$UNIT" -p MainPID --value 2>/dev/null || true)"
case "$PID" in ''|*[!0-9]*) PID=0 ;; esac
BASE=""
if [ "$PID" -gt 0 ] 2>/dev/null; then
  CWD="$(readlink -f "/proc/$PID/cwd" 2>/dev/null || true)"
  if [ -n "$CWD" ] && [ -d "$CWD" ]; then BASE="$CWD"; fi
fi
if [ -z "$BASE" ] || [ ! -d "$BASE" ]; then
  CANDIDATE="$HOME/.local/lib/skeleton-cast"
  if [ -d "$CANDIDATE" ]; then BASE="$CANDIDATE"; fi
fi
if [ -z "$BASE" ] || [ ! -d "$BASE" ]; then
  printf 'LIVE_BASE=UNRESOLVED\n'
  exit 0
fi
printf 'LIVE_BASE=RESOLVED\n'
for NAME in app.py player.py resolver.py; do
  FILE="$BASE/$NAME"
  if [ -f "$FILE" ]; then
    SHA="$(sha256sum "$FILE" | awk '{print $1}')"
    SIZE="$(wc -c < "$FILE" | tr -d ' ')"
    printf 'LIVE_FILE=%s|PRESENT=YES|SHA256=%s|SIZE=%s\n' "$NAME" "$SHA" "$SIZE"
  else
    printf 'LIVE_FILE=%s|PRESENT=NO\n' "$NAME"
  fi
done
python3 - "$BASE" "$HOME" <<'PY_NODE'
from __future__ import annotations

import os
from pathlib import Path
import re
import sys

base = Path(sys.argv[1])
home = Path(sys.argv[2])
source_suffixes = {'.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.json', '.yaml', '.yml', '.toml', '.conf'}
patterns = {
    'AUTOPLAY': re.compile(r'auto[_-]?play|autoplay', re.I),
    'NEXT_EPISODE': re.compile(r'next[_ -]?episode|episode[_ -]?next|play[_ -]?next|nextEpisode', re.I),
    'ENDED': re.compile(r'\bonended\b|\bended\b|media[_ -]?end|playback[_ -]?end', re.I),
    'PLAYLIST': re.compile(r'playlist|queue[_ -]?next|next[_ -]?item', re.I),
    'SETTING': re.compile(r'preference|setting|config', re.I),
}

def safe_name(path: Path) -> str:
    return re.sub(r'[^A-Za-z0-9_.-]', '_', path.name)[:80] or 'unknown'

hits = 0
for path in sorted(base.rglob('*')):
    if hits >= 120 or not path.is_file() or path.suffix.lower() not in source_suffixes:
        continue
    try:
        if path.stat().st_size > 1_000_000:
            continue
        text = path.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        continue
    for lineno, line in enumerate(text.splitlines(), 1):
        cats = [name for name, rx in patterns.items() if rx.search(line)]
        if not cats:
            continue
        print(f"TOKEN_HIT={','.join(cats)}|FILE={safe_name(path)}|LINE={lineno}")
        hits += 1
        if hits >= 120:
            break
print(f'TOKEN_HIT_COUNT={hits}')

state_roots = [home / '.local/state/skeleton-cast', home / '.config/skeleton-cast']
state_hits = 0
bool_rx = re.compile(r'(?i)(?:auto[_-]?play|autoplay)[^\n]{0,80}?\b(true|false|on|off|1|0)\b')
key_rx = re.compile(r'auto[_-]?play|autoplay', re.I)
for root in state_roots:
    if not root.is_dir():
        continue
    for path in sorted(root.rglob('*')):
        if state_hits >= 40 or not path.is_file():
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
            data = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        for lineno, line in enumerate(data.splitlines(), 1):
            if not key_rx.search(line):
                continue
            match = bool_rx.search(line)
            value = 'UNKNOWN'
            if match:
                raw = match.group(1).lower()
                value = 'OFF' if raw in {'false', 'off', '0'} else 'ON'
            print(f'STATE_HIT={safe_name(path)}|LINE={lineno}|VALUE={value}')
            state_hits += 1
            if state_hits >= 40:
                break
print(f'STATE_HIT_COUNT={state_hits}')

proc_counts = {'mpv': 0, 'vlc': 0, 'chromium': 0}
mpv_flags = {'PLAYLIST': False, 'LOOP_FILE': False, 'KEEP_OPEN': False}
proc = Path('/proc')
for item in proc.iterdir() if proc.is_dir() else []:
    if not item.name.isdigit():
        continue
    try:
        comm = (item / 'comm').read_text(errors='ignore').strip().lower()
        cmd = (item / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='ignore').lower()
    except OSError:
        continue
    if 'mpv' in comm or re.search(r'(^|/)mpv(?:\s|$)', cmd):
        proc_counts['mpv'] += 1
        mpv_flags['PLAYLIST'] |= '--playlist' in cmd
        mpv_flags['LOOP_FILE'] |= '--loop-file' in cmd or '--loop=' in cmd
        mpv_flags['KEEP_OPEN'] |= '--keep-open' in cmd
    if 'vlc' in comm:
        proc_counts['vlc'] += 1
    if 'chromium' in comm:
        proc_counts['chromium'] += 1
for name, count in proc_counts.items():
    print(f'PLAYER_PROCESS={name.upper()}|COUNT={count}')
for name, present in mpv_flags.items():
    print(f'MPV_FLAG={name}|PRESENT={"YES" if present else "NO"}')
PY_NODE
'''

SAFE_LINE = re.compile(r'^[A-Z][A-Z0-9_]*(?:=[A-Za-z0-9_.:-]+(?:\|[A-Z0-9_]+=[A-Za-z0-9_.,:-]+)*)?$')

def send(proc: subprocess.Popen[str], payload: dict) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(payload, sort_keys=True, separators=(',', ':')) + '\n')
    proc.stdin.flush()

def recv(proc: subprocess.Popen[str], timeout: float) -> dict:
    assert proc.stdout is not None
    ready, _, _ = select.select([proc.stdout], [], [], timeout)
    if not ready:
        raise RuntimeError('timeout')
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError('eof')
    value = json.loads(line)
    if not isinstance(value, dict):
        raise RuntimeError('invalid_response')
    return value

proc = subprocess.Popen(
    [launcher], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    text=True, bufsize=1,
)
try:
    send(proc, {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'issue-2816-diagnostic','version':'1'}}})
    init = recv(proc, 12)
    if init.get('id') != 1 or 'error' in init:
        raise RuntimeError('initialize_failed')
    send(proc, {'jsonrpc':'2.0','method':'notifications/initialized'})
    send(proc, {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'home_edge_exec','arguments':{
        'node_id':'home-edge-01',
        'execution_lane':'read_only',
        'run_as':'desktop-user',
        'mode':'script',
        'script_interpreter':'bash',
        'script':NODE_SCRIPT,
        'timeout_seconds':90,
        'public':False,
        'idempotency_key':f'issue-2816-live-autoplay-diag-{int(time.time())}',
    }}})
    response = recv(proc, 120)
    if response.get('id') != 2 or 'error' in response:
        raise RuntimeError('tool_call_failed')
    result = response.get('result') or {}
    content = result.get('content') or []
    if not content or not isinstance(content[0], dict):
        raise RuntimeError('missing_content')
    receipt = json.loads(content[0].get('text') or '{}')
    if receipt.get('status') != 'ok':
        print('STATUS=BLOCKED')
        print('ERROR_CLASS=HOME_EDGE_EXEC')
        raise SystemExit(0)
    lines = []
    for raw in str(receipt.get('stdout') or '').splitlines():
        line = raw.strip()
        if SAFE_LINE.fullmatch(line):
            lines.append(line)
    print('STATUS=OK')
    print(f'RECEIPT_HASH={receipt.get("receipt_hash", "NONE")}')
    for line in lines[:180]:
        print(line)
except Exception as exc:
    print('STATUS=BLOCKED')
    print('ERROR_CLASS=' + re.sub(r'[^A-Z0-9_]', '_', type(exc).__name__.upper()))
finally:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
PY

STATUS="$(grep '^STATUS=' "$OUT" | tail -1 | cut -d= -f2 || true)"
{
  echo "### #2816 live autoplay diagnostic"
  echo
  echo '```text'
  sed -n '1,190p' "$OUT"
  echo '```'
} >"$BODY"

URL="$(gh issue comment "$ISSUE" --repo "$REPO_FULL" --body-file "$BODY" 2>/dev/null || true)"
if [ "$STATUS" = "OK" ]; then
  echo "RESULT=DIAGNOSTIC_READY"
else
  echo "RESULT=BLOCKED:diagnostic"
fi
if [ -n "$URL" ]; then
  echo "RECEIPT_REF=$URL"
else
  echo "RECEIPT_REF=NOT_PUBLISHED"
fi
REMOTE

rc=$?
echo "RETURNED_TO_TERMUX=1"
echo "REMOTE_RC=$rc"
exit "$rc"
