from __future__ import annotations
import argparse,json,re,subprocess,sys,urllib.parse,urllib.request
from pathlib import Path
from typing import Any,Mapping
from core.local_inference_adapters import build_default_registry,InferenceValidationError
from core.local_inference_runtime import OllamaClient,InferenceRuntimeError

CRED=Path('/etc/credstore.encrypted/gemini-api-key.cred')

def http(url:str,headers:dict[str,str],body:dict[str,Any]|None=None,timeout:int=120)->dict[str,Any]:
    data=None if body is None else json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()
    req=urllib.request.Request(url,data=data,headers=headers,method='GET' if body is None else 'POST')
    with urllib.request.urlopen(req,timeout=timeout) as r:
        v=json.loads(r.read(2*1024*1024).decode())
    if not isinstance(v,dict): raise RuntimeError('http_result_invalid')
    return v

def gemini_key()->str:
    cp=subprocess.run(['systemd-creds','decrypt','--name=gemini-api-key',str(CRED),'-'],capture_output=True,text=True,timeout=20)
    if cp.returncode or not cp.stdout.strip(): raise RuntimeError('gemini_credential_unavailable')
    return cp.stdout.strip()

def pick_model(key:str)->str:
    models=http('https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000',{'x-goog-api-key':key,'Accept':'application/json'},None,30).get('models',[])
    avail=[]
    for x in models if isinstance(models,list) else []:
        if not isinstance(x,Mapping): continue
        n=str(x.get('name') or ''); methods=x.get('supportedGenerationMethods') or []
        if 'generateContent' in methods and n.startswith('models/') and 'flash' in n.lower() and all(z not in n.lower() for z in ('live','image','tts')): avail.append(n[7:])
    for p in ('gemini-3.5-flash','gemini-3.1-flash-lite','gemini-3.6-flash','gemini-3-flash','gemini-2.5-flash-lite','gemini-2.5-flash'):
        if p in avail:return p
    stable=sorted(x for x in avail if 'preview' not in x and 'exp' not in x)
    if stable:return stable[-1]
    if avail:return sorted(avail)[-1]
    raise RuntimeError('gemini_model_unavailable')

def parse_and_validate(text:str,adapter:Any,payload:Mapping[str,Any])->Mapping[str,Any]:
    t=text.strip()
    if t.startswith('```'): t=re.sub(r'^```(?:json)?\s*|\s*```$','',t,flags=re.I|re.S)
    v=json.loads(t)
    if not isinstance(v,Mapping): raise InferenceValidationError('model_output_not_object')
    return adapter.output_validator(v,payload)

def gemini_generate(prompt:str,schema:Mapping[str,Any],key:str,model:str,timeout:int)->str:
    url='https://generativelanguage.googleapis.com/v1beta/models/'+urllib.parse.quote(model,safe='-._')+':generateContent'
    base={'contents':[{'role':'user','parts':[{'text':prompt}]}]}
    last=None
    for cfg in ({'temperature':0,'responseMimeType':'application/json','responseJsonSchema':dict(schema)},{'temperature':0,'responseMimeType':'application/json'}):
        try:
            r=http(url,{'x-goog-api-key':key,'Content-Type':'application/json'},{**base,'generationConfig':cfg},timeout)
            parts=r['candidates'][0]['content']['parts']; return ''.join(str(x.get('text') or '') for x in parts)
        except Exception as e:last=e
    raise RuntimeError(type(last).__name__ if last else 'gemini_failed')

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('request_type'); ap.add_argument('--model',default='qwen2.5:1.5b'); ap.add_argument('--timeout',type=int,default=120); args=ap.parse_args()
    payload=json.loads(sys.stdin.read());
    if not isinstance(payload,dict): raise SystemExit(2)
    adapter=build_default_registry().get(args.request_type); prompt=adapter.prompt_builder(payload)
    local_out=None; local_reason=None
    try:
        text=OllamaClient('http://127.0.0.1:11434').generate(model=args.model,prompt=prompt,timeout_seconds=args.timeout,format_schema=adapter.output_schema)
        local_out=parse_and_validate(text,adapter,payload)
        if local_out.get('route')=='ACCEPT' and float(local_out.get('confidence') or 0)>=0.90:
            print(json.dumps({'schema':'skeleton.semantic_conveyor.result.v1','request_type':args.request_type,'status':'DONE','provider':'local_ollama','model':args.model,'output':dict(local_out)},ensure_ascii=False,separators=(',',':')));return 0
        local_reason='LOCAL_REVIEW_OR_LOW_CONFIDENCE'
    except Exception as e: local_reason='LOCAL_'+type(e).__name__.upper()
    try:
        key=gemini_key(); model=pick_model(key); text=gemini_generate(prompt,adapter.output_schema or {},key,model,args.timeout); out=parse_and_validate(text,adapter,payload)
        status='REVIEW' if out.get('route')=='REVIEW' else 'DONE'
        print(json.dumps({'schema':'skeleton.semantic_conveyor.result.v1','request_type':args.request_type,'status':status,'provider':'gemini_api','model':model,'local_fallback_reason':local_reason,'output':dict(out)},ensure_ascii=False,separators=(',',':')));return 0
    except Exception as e:
        if local_out is not None:
            print(json.dumps({'schema':'skeleton.semantic_conveyor.result.v1','request_type':args.request_type,'status':'REVIEW','provider':'local_ollama','model':args.model,'local_fallback_reason':'GEMINI_'+type(e).__name__.upper(),'output':dict(local_out)},ensure_ascii=False,separators=(',',':')));return 0
        print(json.dumps({'schema':'skeleton.semantic_conveyor.result.v1','request_type':args.request_type,'status':'ERROR','provider':'none','reason':'MODEL_PIPELINE_FAILED'},separators=(',',':')));return 1
if __name__=='__main__':raise SystemExit(main())
