from __future__ import annotations

import hashlib, json, os, re, shutil, subprocess, tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Mapping, Sequence

TOPICS = (
    '01 identity_and_civil_status','02 migration_and_residence','03 health_and_insurance',
    '04 work_tax_and_business','05 education_and_qualification','06 finance_banking_and_contracts',
    '07 legal_courts_official_correspondence','08 housing_and_utilities','09 transport_and_travel',
)
SERVICE_FOLDERS = ('00 intake','98 duplicates_versions','99 review')
SUPPORTED = {'.pdf','.tif','.tiff','.png','.jpg','.jpeg','.txt','.doc','.docx','.odt','.rtf','.xls','.xlsx','.ods'}
PARTIAL = ('.part','.partial','.tmp','.crdownload')
EVENT_WORDS = {'appointment':'appointment','termin':'appointment','deadline':'deadline','frist':'deadline','expires':'expiration','renewal':'renewal','hearing':'hearing','booking confirmed':'booked_travel','geburtsdatum':'birthday'}
TOPIC_WORDS = {
    TOPICS[0]:('birth certificate','marriage certificate','standesamt'), TOPICS[1]:('aufenthalt','residence permit','visa','jobcenter'),
    TOPICS[2]:('krankenkasse','insurance','medical','arzt'), TOPICS[3]:('steuer','tax','invoice','gewerbe','finanzamt'),
    TOPICS[4]:('schule','university','diploma','zeugnis'), TOPICS[5]:('bank','konto','contract','rechnung','iban'),
    TOPICS[6]:('court','gericht','bescheid','legal'), TOPICS[7]:('rent','miete','wohnung','utility','strom'),
    TOPICS[8]:('booking','flight','train','reise','ticket'),
}
COUNTRIES = {'DE':('deutschland','germany','finanzamt','jobcenter'),'UA':('ukraine','україна','київ'),'IT':('italia','italy'),'FR':('france','français'),'CA':('canada',)}

class IntakeError(RuntimeError):
    def __init__(self, reason: str): super().__init__(reason); self.reason = reason

@dataclass(frozen=True)
class Person:
    person_id: str
    aliases: tuple[str, ...]

@dataclass(frozen=True)
class Config:
    people: tuple[Person, ...]
    archive_root: Path
    state_path: Path
    outbox_path: Path
    memory_command: tuple[str, ...]
    calendar_command: tuple[str, ...]
    settle_seconds: float = 3.0

class State:
    def __init__(self, path: Path): self.path=path; self.data=self._load()
    def _load(self):
        if not self.path.exists(): return {}
        value=json.loads(self.path.read_text('utf-8'))
        if not isinstance(value,dict): raise IntakeError('invalid_state')
        return value
    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd,tmp=tempfile.mkstemp(dir=self.path.parent,prefix=self.path.name+'.')
        with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(self.data,f,sort_keys=True,separators=(',',':')); f.flush(); os.fsync(f.fileno())
        os.chmod(tmp,0o600); os.replace(tmp,self.path)

class Intake:
    def __init__(self,cfg:Config, runner:Callable|None=None):
        if len(cfg.people)!=3 or len({p.person_id for p in cfg.people})!=3: raise IntakeError('exactly_three_people_required')
        if not cfg.memory_command or not cfg.calendar_command: raise IntakeError('adapter_missing')
        self.cfg=cfg; self.runner=runner or self._run; self.state=State(cfg.state_path); self.outbox=State(cfg.outbox_path)

    def plan(self,source:Path,text:str|None=None)->dict:
        source=source.resolve()
        if not source.is_file() or source.suffix.lower() not in SUPPORTED or source.name.lower().endswith(PARTIAL): raise IntakeError('source_unavailable')
        digest=sha256(source); extracted=text if text is not None else local_text(source,self.runner)
        normalized=' '.join(extracted.casefold().split())
        subjects=[p.person_id for p in self.cfg.people if any(a.casefold() in normalized for a in p.aliases)]
        topic=unique_rule(normalized,TOPIC_WORDS); country=unique_rule(normalized,COUNTRIES)
        date_value,date_precision=extract_date(normalized); doc_type=extract_type(normalized,topic); issuer=extract_issuer(extracted)
        ready=len(subjects)==1 and all((topic,country,doc_type,issuer))
        name=visible_name(date_value,date_precision,doc_type or 'document',issuer or 'unknown issuer',source.suffix)
        relative=Path(subjects[0],topic,country,(date_value or 'Без дати')[:4],name) if ready else Path('99 review',name)
        doc_id=hashlib.sha256(('family_documents|'+digest).encode()).hexdigest()
        events=events_from(normalized,date_value,subjects[0] if len(subjects)==1 else 'review',digest)
        record={'schema':'skeleton.family_document_record.v1','document_id':doc_id,'cluster_id':digest,'binary_sha256':digest,'byte_size':source.stat().st_size,
                'corrected_ocr_text':extracted,'raw_ocr_hash':hashlib.sha256(extracted.encode()).hexdigest(),'languages':languages(extracted),
                'principal_subject':subjects[0] if len(subjects)==1 else None,'all_subjects':subjects,'topic':topic,'jurisdiction':country,
                'document_date':date_value,'document_date_precision':date_precision,'document_type':doc_type,'issuer':issuer,
                'field_evidence':{'subjects':subjects,'topic':topic,'jurisdiction':country},'archive_relative_path':str(relative),
                'duplicate_relations':[],'version_relations':[],'event_candidates':events,'semantic_summary':' | '.join(x for x in (doc_type,issuer,topic,country) if x),
                'canonical_source_kind':'canonical_sqlite'}
        return {'ready':ready,'source':source,'digest':digest,'relative':relative,'record':record,'events':events,'key':doc_id}

    def process(self,source:Path,*,dry_run=False,text:str|None=None)->Mapping[str,object]:
        try: plan=self.plan(source,text)
        except IntakeError as exc: return public('BLOCKED',exc.reason)
        if not plan['ready']: return public('REVIEW','review_required',{'planned':1,'review':1,'written':0})
        if dry_run: return public('DONE','done',{'planned':1,'written':0,'calendar_events':len(plan['events'])})
        key=plan['key']; entry=self.state.data.setdefault(key,{'state':'DISCOVERED'})
        archive=self.cfg.archive_root/plan['relative']; archive.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if entry['state']=='DISCOVERED':
            shutil.copyfile(plan['source'],archive)
            if sha256(archive)!=plan['digest']: raise IntakeError('archive_failed')
            entry.update(state='ARCHIVED',archive_verified=True); self.state.save()
        if entry['state']=='ARCHIVED':
            receipt=self._adapter(self.cfg.memory_command,{'command':'skeleton.memory.private_mutate','operation':'put','dataset':'family_documents','fact_key':'family_document:'+key,'idempotency_key':key+':v1','value':plan['record']},'memory_failed')
            entry.update(state='MEMORY_COMMITTED',memory_receipt=receipt); self.state.save()
        self.outbox.data.setdefault(key+':projection',{'status':'PENDING','attempts':0,'dataset':'family_documents','fact_key':'family_document:'+key,'value_hash':stable_hash(plan['record'])}); self.outbox.save()
        if entry['state']=='MEMORY_COMMITTED':
            for event in plan['events']: self._adapter(self.cfg.calendar_command,{'operation':'upsert','idempotency_key':event['uid'],'event':event},'calendar_failed')
            entry.update(state='DONE',calendar_count=len(plan['events'])); self.state.save()
        return public('DONE','projection_degraded',{'planned':1,'written':1,'calendar_events':len(plan['events']),'projection_pending':1})

    def _adapter(self,command,payload,reason):
        code,out,_=self.runner(command,json.dumps(payload,ensure_ascii=False,separators=(',',':')))
        if code: raise IntakeError(reason)
        try: value=json.loads(out)
        except json.JSONDecodeError: raise IntakeError(reason)
        if not isinstance(value,dict) or value.get('status') not in {'DONE','ACCEPTED','IDEMPOTENT'}: raise IntakeError(reason)
        return value
    @staticmethod
    def _run(command,input_text):
        try: p=subprocess.run(list(command),input=input_text,text=True,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120,check=False)
        except (OSError,subprocess.TimeoutExpired): return 1,'','adapter unavailable'
        return p.returncode,p.stdout[:1000000],p.stderr[:4096]

def stable_file(path:Path,observed:dict,now:float,settle:float)->bool:
    s=path.stat(); current=(s.st_size,s.st_mtime_ns); previous=observed.get(str(path)); observed[str(path)]={'size':s.st_size,'mtime_ns':s.st_mtime_ns,'seen_at':now}
    return bool(previous and (previous['size'],previous['mtime_ns'])==current and now-previous['seen_at']>=settle and s.st_size>0)
def inventory(roots:Sequence[Path],limit=10000):
    out=[]
    for root in roots:
        root=root.resolve()
        for current,dirs,files in os.walk(root,followlinks=False):
            dirs[:]=[d for d in sorted(dirs) if d.casefold() not in {'.git','.ssh','secrets','node_modules','__pycache__'}]
            for name in sorted(files):
                p=Path(current,name)
                if p.suffix.lower() in SUPPORTED and not name.lower().endswith(PARTIAL): out.append(p)
                if len(out)>=limit:return tuple(out)
    return tuple(out)
def local_text(path:Path,runner):
    if path.suffix.lower()=='.txt': return path.read_text('utf-8')
    cmd=('pdftotext','-layout',str(path),'-') if path.suffix.lower()=='.pdf' else ('tesseract',str(path),'stdout','-l','eng+deu+ukr')
    code,out,_=runner(cmd,None)
    if code or not out.strip(): raise IntakeError('ocr_failed')
    return out[:2000000]
def unique_rule(text,rules):
    scores=[(sum(1 for w in words if w in text),name) for name,words in rules.items()]
    scores=[item for item in scores if item[0]]; scores.sort(reverse=True)
    return scores[0][1] if scores and (len(scores)==1 or scores[0][0]>scores[1][0]) else None
def extract_date(text):
    m=re.search(r'\b(20\d{2}|19\d{2})[-/.](0[1-9]|1[0-2])[-/.]([0-2]\d|3[01])\b',text)
    if m:
        try:return date(*map(int,m.groups())).isoformat(),'day'
        except ValueError:pass
    m=re.search(r'\b(20\d{2}|19\d{2})[-/.](0[1-9]|1[0-2])\b',text)
    if m:return f'{m.group(1)}-{m.group(2)}','month'
    m=re.search(r'\b(20\d{2}|19\d{2})\b',text); return (m.group(1),'year') if m else (None,None)
def extract_type(text,topic):
    for name,words in {'invoice':('invoice','rechnung'),'official notice':('bescheid','decision notice'),'contract':('contract','vertrag'),'appointment letter':('appointment','termin'),'travel booking':('booking confirmed','reservation')}.items():
        if any(w in text for w in words): return name
    return topic.split(' ',1)[1].replace('_',' ') if topic else None
def extract_issuer(text):
    for line in text.splitlines()[:20]:
        m=re.match(r'(?i)(issuer|from|absender|herausgeber)\s*[:\-]\s*(.{2,80})$',line.strip())
        if m:return m.group(2).strip()
    return None
def visible_name(value,precision,kind,issuer,suffix):
    prefix=value if precision else 'Без дати'; clean=lambda s:re.sub(r'[^A-Za-z0-9ÄÖÜäöüßА-Яа-яІіЇїЄє ._-]+','',s).strip()[:80] or 'unknown'
    return f'{prefix} — {clean(kind)} — {clean(issuer)}{suffix.lower()}'
def events_from(text,value,subject,digest):
    if not value or len(value)!=10:return []
    out=[]
    for word,kind in EVENT_WORDS.items():
        if word in text:
            uid=hashlib.sha256(f'{digest}|{kind}|{value}|{subject}'.encode()).hexdigest(); out.append({'uid':uid,'event_type':kind,'date':value,'subject':subject,'privacy':'private','attendees':[],'conference':None})
    return out
def languages(text):
    out=[]
    if re.search(r'[А-Яа-яІіЇїЄє]',text):out.append('uk')
    if re.search(r'[ÄÖÜäöüß]',text):out.append('de')
    if re.search(r'[A-Za-z]',text):out.append('en')
    return out or ['und']
def sha256(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()
def stable_hash(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def public(status,reason,counts=None):
    allowed={'done','review_required','projection_degraded','source_unavailable','ocr_failed','archive_failed','memory_failed','calendar_failed','exactly_three_people_required','adapter_missing'}
    return {'schema':'skeleton.family_document_receipt.public.v1','status':status,'reason_code':reason if reason in allowed else 'source_unavailable','counts':dict(counts or {})}
