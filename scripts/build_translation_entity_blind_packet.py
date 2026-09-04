#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

SKIP_TOKENS=(".translation.aligned", ".translation.quality", ".summary.uk")


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')


def source_rows(root: Path):
    eligible=[]; missing=[]
    for meta_path in sorted(root.rglob('*.json')):
        if any(t in meta_path.name for t in SKIP_TOKENS):
            continue
        try: meta=json.loads(meta_path.read_text(encoding='utf-8'))
        except Exception:
            continue
        if not isinstance(meta,dict) or meta.get('schema')!='skeleton.document.v2':
            continue
        txt=Path(str(meta.get('txt') or ''))
        meta_hash=hashlib.sha256(str(meta_path).encode()).hexdigest()
        if not txt.is_file():
            missing.append({'metadata_ref_sha256':meta_hash,'reason':'source_text_missing'})
            continue
        text=txt.read_text(encoding='utf-8',errors='replace')
        source_hash=hashlib.sha256(text.encode('utf-8')).hexdigest()
        eligible.append((source_hash,text,meta_hash))
    return eligible,missing


def build(root: Path, out: Path, *, seed_hex: str | None=None):
    rows,missing=source_rows(root)
    seed=bytes.fromhex(seed_hex) if seed_hex else secrets.token_bytes(32)
    ordered=sorted(rows,key=lambda r:hmac.new(seed,r[0].encode(),hashlib.sha256).digest())
    packet=[]
    for i,(source_hash,text,meta_hash) in enumerate(ordered,1):
        packet.append({
            'packet_index':i,
            'blind_document_id':hashlib.sha256((source_hash+seed.hex()).encode()).hexdigest()[:20],
            'source_sha256':source_hash,
            'raw_ocr_text':text,
            'classes_to_annotate':['amount','date','case_reference','iban_account','medical_code','name','address'],
            'extractor_output_visible':False,
        })
    out.mkdir(parents=True,exist_ok=True)
    (out/'packet.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in packet),encoding='utf-8')
    manifest={
        'schema':'skeleton.translation.entity_blind_packet.v1',
        'created_at':now(),
        'status':'BLIND_PACKET_READY_PARTIAL' if missing else 'BLIND_PACKET_READY_COMPLETE',
        'production_metadata_count':len(rows)+len(missing),
        'packet_document_count':len(rows),
        'missing_source_text_count':len(missing),
        'ordering':'HMAC-SHA256(seed, source_sha256); independent of extractor output/filesystem/date',
        'extractor_output_in_packet':False,
        'seed_sha256':hashlib.sha256(seed).hexdigest(),
        'seed_file':'seed.hex',
        'classes':['amount','date','case_reference','iban_account','medical_code','name','address'],
        'residual_bias_note':'Prior human exposure cannot be erased; packet randomization and extractor blindness reduce but do not eliminate memory anchoring.',
        'missing':missing,
    }
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (out/'seed.hex').write_text(seed.hex()+'\n',encoding='ascii')
    return manifest


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--root',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--seed-hex')
    args=ap.parse_args()
    print(json.dumps(build(args.root,args.out,seed_hex=args.seed_hex),ensure_ascii=False))

if __name__=='__main__': main()
