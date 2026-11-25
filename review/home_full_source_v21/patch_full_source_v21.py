"""REVIEW: reject short unverified clips; use Ukrainian-first implicit rebind.
Apply only to exact reviewed backend. No deployment or catalog writes.
"""
import ast,hashlib
BASE='110359cf65199bf65dca896b83fe21091e2e3fbd432ac0c2451ff2f942094291'
def once(s,a,b):
    if s.count(a)!=1:raise ValueError(a[:100])
    return s.replace(a,b,1)
def patch(s):
    assert hashlib.sha256(s.encode()).hexdigest()==BASE
    helpers='''def _home_full_source_blocked(source: dict) -> bool:
    # Same conservative duration floor already used by autoplay. A short
    # unverified clip is not automatically declared a trailer or deleted.
    return (not isinstance(source,dict) or media_discovery.is_trailer_source(source)
            or _autoplay_is_trailer(source))


def _home_implicit_audio_rank(source: dict) -> int:
    return {'uk':2,'ru':1}.get(media_discovery.source_audio_language(source),0)


'''
    s=once(s,'def _media_endpoint_resolve_source(',helpers+'def _media_endpoint_resolve_source(')
    # Apply the admission guard to the catalog, history, selection and playback
    # paths, not unrelated YouTube or IPTV operations.
    targets={'_history_preference','_history_merged_job','video_history_open',
             'video_season_load','get_job','play','_endpoint_video_selection_write',
             '_media_endpoint_resolve_source','_media_endpoint_resume_payload',
             '_selected_video_job_source','_media_history_resolve_stale_source',
             'video_resume','_samsung_compatible_source'}
    t=ast.parse(s);parts=[]
    for n in t.body:
        if isinstance(n,ast.FunctionDef) and n.name in targets:
            old=ast.get_source_segment(s,n)
            new=old.replace('media_discovery.is_trailer_source(', '_home_full_source_blocked(')
            if n.name=='_history_merged_job':
                # Do not mark a duration-only rejected source as an explicit trailer.
                new=once(new,'trailer = _home_full_source_blocked(clone)','trailer = media_discovery.is_trailer_source(clone)')
                new=once(new,"clone['source_type'] = 'trailer' if trailer else 'full_release'","clone['source_type'] = 'trailer' if trailer else ('unverified_clip' if _home_full_source_blocked(clone) else 'full_release')")
                new=once(new,"        source_state = 'trailer_only' if merged else 'no_source'","        source_state = 'trailer_only' if any(media_discovery.is_trailer_source(x) for x in merged) else 'no_source'")
                new=once(new,"'content_type': str(item.get('content_type') or ''), 'sources': merged,","'content_type': str(item.get('content_type') or ''), 'sources': full_sources,")
            if n.name=='get_job':
                new=once(new,"    for source in safe.get('sources', []):","    safe['sources']=[x for x in safe.get('sources',[]) if not _home_full_source_blocked(x)]\n    if not safe['sources'] and safe.get('status')=='ready':\n        safe['status']='no_verified_sources'\n    for source in safe.get('sources', []):")
            if n.name in ('_media_endpoint_resolve_source','play'):
                prefix="        " if n.name=='_media_endpoint_resolve_source' else "            "
                anchor=prefix+'if quality:'
                new=once(new,anchor,prefix+'if not voice and candidates:\n'+prefix+'    best_language=max(_home_implicit_audio_rank(x) for x in candidates)\n'+prefix+'    candidates=[x for x in candidates if _home_implicit_audio_rank(x)==best_language]\n'+anchor)
            parts.append((old,new))
    for old,new in parts:s=once(s,old,new)
    # Catalog distinguishes actual trailers from clips that have no verified
    # full duration; original jobs and hidden/deleted preferences are untouched.
    t=ast.parse(s);n=next(n for n in t.body if isinstance(n,ast.FunctionDef) and n.name=='_history_item')
    old=ast.get_source_segment(s,n);new=once(old,'            else:\n                full_sources += 1','            elif not _home_full_source_blocked(source):\n                full_sources += 1');s=once(s,old,new)
    n=next(n for n in ast.parse(s).body if isinstance(n,ast.FunctionDef) and n.name=='_video_selection_write')
    old=ast.get_source_segment(s,n)
    new=once(old,"        if source_id and not any(str(item.get('source_id') or '') == source_id for item in job.get('sources', [])):","        if source_id and not any(str(item.get('source_id') or '') == source_id and not _home_full_source_blocked(item) for item in job.get('sources', [])):")
    s=once(s,old,new)
    compile(s,'<review>','exec')
    return s
