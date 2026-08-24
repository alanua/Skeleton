#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime, timezone
import hashlib, os, shutil, subprocess

ROOT = Path('/home/agent/agent-dev/worktrees/skeleton')
AUDIT_DIR = Path('/home/agent/.local/state/skeleton/private-audits')
CHECK = [ROOT, Path('/home/agent/agent-dev/repos'), Path('/home/agent/.cache'), Path('/home/agent/.npm'), Path('/home/agent/.local')]

def size(p: Path) -> int:
    r = subprocess.run(['du','-sx','--block-size=1','--',str(p)], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try: return int(r.stdout.split()[0]) if r.returncode == 0 else 0
    except Exception: return 0

AUDIT_DIR.mkdir(parents=True, exist_ok=True)
os.chmod(AUDIT_DIR, 0o700)
now = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
audit = AUDIT_DIR / f'runner-disk-inventory-{now}.log'
rows = []
usage = shutil.disk_usage('/')
print('STATUS=OK')
print(f'FREE_GIB={usage.free/1024**3:.2f}')
for p in CHECK:
    if p.exists():
        b = size(p); rows.append(f'{p}\t{b}'); print(f'{p.name.upper()}_GIB={b/1024**3:.2f}')
children=[]
if ROOT.is_dir() and not ROOT.is_symlink():
    for p in ROOT.iterdir():
        if p.is_dir() and not p.is_symlink():
            b=size(p); children.append((b,p.name)); rows.append(f'{p}\t{b}')
children.sort(reverse=True)
print('TOP=')
for b,n in children[:25]: print(f'{n} {b/1024**3:.2f}G')
audit.write_text('\n'.join(rows)+'\n', encoding='utf-8')
os.chmod(audit,0o600)
print('AUDIT_SHA256='+hashlib.sha256(audit.read_bytes()).hexdigest())
