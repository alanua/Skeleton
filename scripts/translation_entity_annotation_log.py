#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')


def append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',encoding='utf-8') as f:
        f.write(json.dumps(record,ensure_ascii=False)+'\n')
    os.chmod(path,0o600)


def load(path: Path) -> list[dict]:
    if not path.is_file(): return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def main() -> None:
    ap=argparse.ArgumentParser()
    ap.add_argument('--log',type=Path,required=True)
    sub=ap.add_subparsers(dest='cmd',required=True)
    s=sub.add_parser('start'); s.add_argument('--document-id',required=True); s.add_argument('--reviewer',default='Oleksii/operator'); s.add_argument('--classes',required=True); s.add_argument('--source-quality-review',action='store_true')
    e=sub.add_parser('end'); e.add_argument('--session-id',required=True); e.add_argument('--completion',choices=['COMPLETE','PARTIAL','ABANDONED'],required=True); e.add_argument('--unresolved',type=int,default=0)
    r=sub.add_parser('report')
    args=ap.parse_args()
    if args.cmd=='start':
        sid=str(uuid.uuid4())
        rec={'event':'START','session_id':sid,'document_id':args.document_id,'reviewer':args.reviewer,'classes':[x for x in args.classes.split(',') if x],'source_quality_requires_review':args.source_quality_review,'wall_started_at':now(),'monotonic_started':time.monotonic()}
        append(args.log,rec); print(json.dumps({'session_id':sid},ensure_ascii=False)); return
    if args.cmd=='end':
        rows=load(args.log); starts=[x for x in rows if x.get('event')=='START' and x.get('session_id')==args.session_id]
        if not starts: raise SystemExit('unknown session_id')
        if any(x.get('event')=='END' and x.get('session_id')==args.session_id for x in rows): raise SystemExit('session already ended')
        st=starts[-1]; elapsed=max(0.0,time.monotonic()-float(st['monotonic_started']))
        rec={'event':'END','session_id':args.session_id,'document_id':st['document_id'],'reviewer':st['reviewer'],'wall_ended_at':now(),'elapsed_active_seconds':round(elapsed,3),'completion':args.completion,'unresolved_case_count':args.unresolved,'classes':st['classes'],'source_quality_requires_review':st['source_quality_requires_review']}
        append(args.log,rec); print(json.dumps(rec,ensure_ascii=False)); return
    rows=load(args.log); ends=[x for x in rows if x.get('event')=='END']
    by_class={}; total=0.0
    for x in ends:
        if x.get('completion')=='ABANDONED': continue
        sec=float(x.get('elapsed_active_seconds') or 0); total+=sec
        for c in x.get('classes') or []:
            d=by_class.setdefault(c,{'sessions':0,'active_seconds':0.0})
            d['sessions']+=1; d['active_seconds']+=sec
    print(json.dumps({'completed_or_partial_sessions':sum(1 for x in ends if x.get('completion')!='ABANDONED'),'active_seconds_total':round(total,3),'by_class':by_class},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
