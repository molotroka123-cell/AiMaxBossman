"""Studio lifecycle: the existing Images worker owns admission and cancellation."""
from __future__ import annotations
import asyncio
import contextlib
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from uuid import uuid4
import sqlalchemy as sa
from bcc.db import utcnow, rows_dicts
from bcc.v2.images_tables import image_jobs, image_assets, image_collections
from bcc.v2.images_runtime import ImageStorage, MockImageProvider
from bcc.studio import catalog
from bcc.studio.tables import jobs, runs, config, budget
from bcc.studio.provider import GenerationPlane, ProviderFailure, ProviderStatus

class StudioError(ValueError):
    def __init__(self,reason,message,verdict='FAIL'):
        self.reason,self.verdict=reason,verdict
        super().__init__(message)


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def storage(svc): return ImageStorage(svc.settings.data_dir/'studio')

async def one(svc,table,key,value):
    async with svc.db.session() as s:
        row=(await s.execute(sa.select(table).where(key==value))).first()
    return dict(row._mapping) if row else None

async def setup(svc):
    from bcc.studio.integrations import register
    if not getattr(svc,'_studio_registered',False):register(svc);svc._studio_registered=True
    # Mutable organization is deliberately separate from immutable evidence columns.
    async with svc.db.session() as s:
        await s.execute(sa.text('''CREATE TRIGGER IF NOT EXISTS studio_evidence_guard
        BEFORE UPDATE OF job_id,legacy_asset_id,surface,model,provenance,file_path,sha256,file_bytes,mime,created_at ON studio_runs
        BEGIN SELECT RAISE(ABORT,'studio provenance is immutable'); END'''))
        await s.execute(sa.text('''CREATE TRIGGER IF NOT EXISTS studio_evidence_delete_guard
        BEFORE DELETE ON studio_runs BEGIN SELECT RAISE(ABORT,'use studio trash'); END'''))
        active=list((await s.execute(sa.select(image_jobs.c.id).join(jobs,jobs.c.job_id==image_jobs.c.id).where(image_jobs.c.status=='running'))).scalars())
        if active:
            await s.execute(sa.update(image_jobs).where(image_jobs.c.id.in_(active)).values(status='failed',error='interrupted_unknown: inspect provider before retry',finished_at=utcnow()))
            await s.execute(sa.update(jobs).where(jobs.c.job_id.in_(active)).values(reason='interrupted_unknown',verdict='OWNER_REQUIRED'))
        await s.commit()
    # Engine child processes recorded by sdcpp sidecars whose owner process died: kill by verified PID identity.
    try:
        from bcc.studio.providers.sdcpp import reconcile_orphans
        await asyncio.to_thread(reconcile_orphans,svc.settings.data_dir/'studio'/'engine-work')
    except Exception:pass


def model_specs():
    models=catalog.load()['models']
    local=next(m for m in models if m['provider']=='comfyui')
    mock={**local,'id':'mock:image','provider':'mock','label':'Локальное демо (не AI-генерация)','status':'DEMO','enabled':True}
    reframe={**local,'id':'local:reframe','provider':'local','label':'Рефрейм FFmpeg','enabled':True,'status':'LOCAL','roles':{'reference':1},'settings':{'width':{'type':'range','min':256,'max':4096,'default':1024,'integer':True,'multiple_of':2},'height':{'type':'range','min':256,'max':4096,'default':1024,'integer':True,'multiple_of':2},'mode':{'type':'enum','values':['pad','crop'],'default':'pad'}}}
    return [mock,*models,reframe]

async def provider_fingerprint(svc,model):
    if model.startswith('openrouter:'):
        from bcc.v2.openrouter_identity import resolve
        from bcc.studio.governance import policy
        credential=await resolve(svc.db,svc.vault)
        connection={'policy':await policy(svc),'credential_digest':hashlib.sha256((credential.key or '').encode()).hexdigest()}
    elif model.startswith('comfyui:'):
        from bcc.oss.comfyui import image_configuration
        connection={'local':image_configuration()}
    elif model.startswith('sdcpp:'):
        from bcc.studio.providers.sdcpp import configuration
        cfg=configuration()
        connection={'local':None if cfg is None else {'bin':str(cfg['bin']),'manifest':cfg['manifest']}}
    else:connection={}
    return digest({'model':model,'connection':connection,'catalog':catalog.load()})

async def models(svc):
    from bcc.oss.comfyui import image_configuration
    from bcc.studio.governance import policy
    from bcc.v2.openrouter_identity import resolve
    from datetime import datetime, timezone
    import shutil
    p=await policy(svc)
    credential=await resolve(svc.db,svc.vault) if p['enabled'] else None
    out=[]
    for m in model_specs():
        available=m['provider']=='mock'
        if m['provider']=='local':available=bool(shutil.which('ffmpeg'))
        if m['provider']=='comfyui':
            try:available=image_configuration() is not None
            except ValueError:available=False
        if m['provider']=='sdcpp':
            from bcc.studio.providers.sdcpp import configuration,engine_files
            try:
                cfg=configuration();available=cfg is not None and bool(engine_files(cfg,m['id']))
            except (ValueError,OSError,KeyError):available=False
        if m['provider']=='openrouter':
            price=p['prices'].get(m['id'])
            available=bool(p['enabled'] and credential and credential.configured and not credential.conflicts and price is not None and (not p['free_only'] or (price==0 and catalog.declared_free(m['id']) is True)))
        proof=await one(svc,config,config.c.key,'probe:'+m['id'])
        verified=False
        if proof and available:
            try:
                age=(datetime.now(timezone.utc)-datetime.fromisoformat(proof['value']['at'])).total_seconds()
                row=await one(svc,runs,runs.c.id,proof['value']['run_id'])
                if proof['value'].get('configuration')==await provider_fingerprint(svc,m['id']) and 0<=age<=86400 and row and not row['deleted'] and not row['provenance']['mock'] and row['model']==m['id']:
                    await verified_handle(svc,row,close=True);verified=True
            except (ValueError,KeyError,OSError,RuntimeError):pass
        out.append({**m,'configured':available,'available':verified or (available and m['provider'] in ('mock','local','sdcpp')),'verified':verified,'verification':'PASS' if verified else 'NOT_RUN','demo':m['provider']=='mock'})
    return out

async def generation_status(svc):
    available=[m for m in await models(svc) if m['verified'] and m['available'] and m['surface'] in ('image','video')]
    return {'status':'AVAILABLE' if available else 'BLOCKED','reason':'Verified Studio results on this installation' if available else 'No verified image/video generation provider is connected to Video Studio','cloud_used':False,'models':[m['id'] for m in available]}

async def validate_plane(svc,payload):
    model=next((m for m in model_specs() if m['id']==payload['model']),None)
    if model is None: raise ValueError('model: unknown id')
    settings=catalog.validate_settings(model,payload.get('settings',{}))
    if 'seed' in settings and settings['seed'] is None: settings['seed']=secrets.randbelow(2**31)
    media=payload.get('media',[])
    counts={}
    normalized=[]
    for item in media:
        role=item['role']; counts[role]=counts.get(role,0)+1
        if role not in model['roles'] or counts[role]>model['roles'][role]: raise ValueError(f'media.{role}: unsupported role/count')
        row=await one(svc,runs,runs.c.id,item['run_id'])
        if not row or row['deleted']: raise ValueError('media.run_id: missing or deleted')
        await verified_handle(svc,row,close=True)
        normalized.append({'run_id':row['id'],'role':role,'sha256':row['sha256']})
    if model['id']=='local:reframe':
        if len(normalized)!=1:raise ValueError('media: reframe needs one reference')
        model={**model,'surface':(await one(svc,runs,runs.c.id,normalized[0]['run_id']))['surface']}
    cid=payload.get('collection_id')
    if cid is not None and not await one(svc,image_collections,image_collections.c.id,cid): raise ValueError('collection_id: missing')
    return model,{'model':model['id'],'prompt':payload['prompt'],'settings':settings,'media':normalized,'count':payload.get('count',1),'collection_id':cid}

async def create_job(svc,payload):
    model,plane=await validate_plane(svc,payload)
    # All side effects go through this queue; unknown/disabled providers fail before dispatch.
    async with svc.db.session() as s:
        result=await s.execute(sa.insert(image_jobs).values(prompt=plane['prompt'],model_alias=model['id'],count=plane['count'],collection_id=plane['collection_id'],options={'studio':True},status='queued',created_at=utcnow(),updated_at=utcnow()))
        jid=int(result.inserted_primary_key[0])
        await s.execute(sa.insert(jobs).values(job_id=jid,plane=plane,surface=model['surface'],cost_usd='NOT_CAPTURED:not_dispatched'))
        await s.commit()
    await svc.bus.emit('studio.job.queued',job_id=jid)
    return await get_job(svc,jid)

async def get_job(svc,jid):
    row=await one(svc,image_jobs,image_jobs.c.id,jid)
    ext=await one(svc,jobs,jobs.c.job_id,jid)
    if not row or not ext: raise KeyError('studio job not found')
    return {**row,'studio':ext}

async def fail(svc,jid,reason,message,verdict='FAIL'):
    partial=[]
    async with svc.db.session() as s:
        updated=await s.execute(sa.update(image_jobs).where(image_jobs.c.id==jid,image_jobs.c.status=='running').values(status='failed',error=message,finished_at=utcnow(),updated_at=utcnow()))
        if updated.rowcount:
            await s.execute(sa.update(jobs).where(jobs.c.job_id==jid).values(reason=reason,verdict=verdict))
            # Red team 2026-09-21 (RT-S5): задание, упавшее на втором выходе, оставляло
            # первый выход в галерее как обычный run. Незавершённое задание не
            # даёт результатов: частичные выходы уходят в корзину, файлы удаляются.
            # Исключение — перерасход бюджета: байты уже оплачены и проверены,
            # выбрасывать их — второй ущерб (test_studio_cloud); они остаются.
            partial=[] if reason=='budget' else [dict(r._mapping) for r in (await s.execute(sa.select(runs.c.id,runs.c.file_path).where(runs.c.job_id==jid,runs.c.deleted==False))).all()]  # noqa: E712
            if partial:
                await s.execute(sa.update(runs).where(runs.c.id.in_([r['id'] for r in partial])).values(deleted=True))
        await s.commit()
    for r in partial:
        try:Path(r['file_path']).unlink(missing_ok=True)
        except OSError:pass
    await svc.bus.emit('studio.job.failed',job_id=jid,reason=reason,partial_outputs_trashed=len(partial))

def _magic(path):
    with path.open('rb') as stream:return stream.read(8)

async def verify_file(path,surface,*,mock=False):
    from bcc.video_studio.media import probe, digest_file, process, binary, input_args, file_kind
    if not path.is_file() or not 0<path.stat().st_size<=256*1024*1024: raise ValueError('output: missing/empty/too large')
    if mock:
        import xml.etree.ElementTree as ET
        raw=path.read_bytes()
        root=ET.fromstring(raw)
        if root.tag!='{http://www.w3.org/2000/svg}svg': raise ValueError('mock: invalid SVG')
        for node in root.iter():
            if node.tag.split('}')[-1] not in {'svg','rect','text','defs','linearGradient','radialGradient','stop','circle','path','g'}: raise ValueError('mock: unsafe SVG node')
            if any(k.lower().startswith('on') or 'href' in k.lower() for k in node.attrib): raise ValueError('mock: unsafe SVG attribute')
        meta={'width':int(root.attrib['width']),'height':int(root.attrib['height']),'duration_ms':None,'mime':'image/svg+xml'}
    elif _magic(path)==b'\x89PNG\r\n\x1a\n':
        from bcc.oss.comfyui import verify_png
        w,h=verify_png(path.read_bytes());meta={'width':w,'height':h,'duration_ms':None,'mime':'image/png'}
        if surface!='image': raise ValueError('surface mismatch')
    else:
        kind,extension=file_kind(path)
        if surface=='image' and kind!='jpeg_pipe':raise ValueError('image container mismatch')
        if surface=='video' and kind not in ('mov','matroska','avi'):raise ValueError('video container mismatch')
        info=await probe(path)
        if surface=='image' and not info['has_video']: raise ValueError('image stream missing')
        if surface=='video' and (not info['has_video'] or info['duration_ticks']<=0): raise ValueError('video duration/stream missing')
        if surface=='audio' and (not info['has_audio'] or info['has_video'] or info['duration_ticks']<=0): raise ValueError('audio duration/stream missing')
        # Decoding, not just ffprobe metadata, is the completion boundary.
        await process([binary('ffmpeg'),'-v','error',*input_args(path),'-f','null','-'],timeout=90)
        meta={'width':info['width'],'height':info['height'],'duration_ms':info['duration_ticks']/1000,'mime':{'.jpg':'image/jpeg','.mp4':'video/mp4','.mkv':'video/x-matroska','.avi':'video/x-msvideo','.mp3':'audio/mpeg','.wav':'audio/wav','.flac':'audio/flac','.ogg':'audio/ogg'}[extension]}
    return {**meta,'path':str(path),'sha256':await asyncio.to_thread(digest_file,path),'bytes':path.stat().st_size}

async def persist(svc,jid,plane,model,path,*,mock=False,request_id=None,cost='NOT_CAPTURED:provider_did_not_report',legacy=None,effective_settings=None):
    output=await verify_file(path,model['surface'],mock=mock)
    if plane['settings'].get('width') and output['width']!=plane['settings']['width']: raise ValueError('output.width mismatch')
    if plane['settings'].get('height') and output['height']!=plane['settings']['height']: raise ValueError('output.height mismatch')
    rid=uuid4().hex
    provenance={'plane':plane,'settings_resolved':effective_settings or plane['settings'],'provider':model['provider'],'model':model['id'],'mock':mock,'output':output,'inputs':plane['media'],'provider_request_id':request_id,'cost_usd':cost,'finished_at':utcnow().isoformat(),'harness':{'repository_sha':os.environ.get('BCC_ACCEPTANCE_SOURCE_SHA','NOT_CAPTURED:development'),'catalog_sha256':digest(catalog.load())}}
    async with svc.db.session() as s:
        if jid is not None:
            claim=await s.execute(sa.update(image_jobs).where(image_jobs.c.id==jid,image_jobs.c.status=='running').values(updated_at=utcnow()))
            if not claim.rowcount: return None
        await s.execute(sa.insert(runs).values(id=rid,job_id=jid,legacy_asset_id=legacy,surface=model['surface'],model=model['id'],provenance=provenance,file_path=str(path),sha256=output['sha256'],file_bytes=output['bytes'],mime=output['mime'],collection_id=plane.get('collection_id')))
        await s.commit()
    await svc.bus.emit('studio.run.created',run_id=rid)
    return rid

async def _generate(svc,job,ext):
    plane=ext['plane']; model=next(m for m in model_specs() if m['id']==plane['model'])
    if model['provider']=='mock':
        for index in range(plane['count']):
            data,_,_=await MockImageProvider().render({**plane['settings'],'prompt':plane['prompt']},index)
            path=storage(svc).save(f"generated/{job['id']}-{index}.svg",data)
            rid=await persist(svc,job['id'],plane,model,path,mock=True,cost=0)
            if rid is None: path.unlink(missing_ok=True);return
    elif model['id']=='local:reframe':
        from bcc.studio.integrations import process_reframe
        await process_reframe(svc,job,ext,model)
    else:
        from bcc.studio.dispatch import generate
        await generate(svc,job,ext,model)
    async with svc.db.session() as s:
        count=(await s.execute(sa.select(sa.func.count()).select_from(runs).where(runs.c.job_id==job['id']))).scalar_one()
        if count < plane['count']:raise StudioError('malformed','Provider returned fewer verified outputs than requested')
        update=await s.execute(sa.update(image_jobs).where(image_jobs.c.id==job['id'],image_jobs.c.status=='running').values(status='completed',progress=1,finished_at=utcnow()))
        if update.rowcount: await s.execute(sa.update(jobs).where(jobs.c.job_id==job['id']).values(verdict='PASS' if model['provider']!='mock' else 'PARTIAL'))
        await s.commit()
    await svc.bus.emit('studio.job.completed',job_id=job['id'])

async def process_claimed(svc,job):
    ext=await one(svc,jobs,jobs.c.job_id,job['id'])
    if not ext: return await fail(svc,job['id'],'malformed','studio metadata missing')
    task=asyncio.create_task(_generate(svc,job,ext))
    try:
        budget=next(m['deadline_seconds'] for m in model_specs() if m['id']==ext['plane']['model'])
        if ext['plane']['model'].startswith('sdcpp:'):
            # A segment chain (length 10s/15s/30s) runs one engine pass per segment.
            from bcc.studio.providers.sdcpp import segments_for
            budget*=segments_for(ext['plane'].get('settings') or {})
        deadline=time.monotonic()+budget
        while not task.done():
            await asyncio.wait({task},timeout=0.25)
            row=await one(svc,image_jobs,image_jobs.c.id,job['id'])
            if row['status']!='running' and not task.done(): task.cancel();return
            if time.monotonic()>deadline: task.cancel();raise StudioError('timeout','Generation deadline exceeded')
        task.result()
    except ProviderFailure as exc: await fail(svc,job['id'],exc.status.reason,str(exc),exc.status.verdict)
    except StudioError as exc: await fail(svc,job['id'],exc.reason,str(exc),exc.verdict)
    except Exception as exc:
        # Do not expose raw provider/network exception text or credentials.
        await fail(svc,job['id'],'malformed',f'Generation rejected ({type(exc).__name__}); inspect configuration')
    finally:
        if not task.done(): task.cancel()
        with contextlib.suppress(BaseException): await task

async def migrate_legacy(svc):
    async with svc.db.session() as s:
        rows=rows_dicts((await s.execute(sa.select(image_assets).where(~image_assets.c.id.in_(sa.select(runs.c.legacy_asset_id).where(runs.c.legacy_asset_id.is_not(None)))))).all())
    for row in rows:
        try:
            path=ImageStorage(svc.settings.data_dir/'images').resolve_existing(row['file_path'])
            mock=row['model_alias']=='mock-image'
            plane={'model':row['model_alias'],'prompt':row['prompt'] or '', 'settings':{},'media':[],'count':1,'collection_id':row['collection_id']}
            # Old imports without decodable bytes are not silently blessed.
            rid=await persist(svc,None,plane,{'id':row['model_alias'],'provider':row['model_alias'],'surface':'image'},path,mock=mock,cost='NOT_CAPTURED:pre-v8',legacy=row['id'])
            async with svc.db.session() as s:
                await s.execute(sa.update(runs).where(runs.c.id==rid).values(favorite=row['favorite'],deleted=row['status']=='deleted'))
                await s.commit()
        except (ValueError,RuntimeError,FileNotFoundError,PermissionError,sa.exc.IntegrityError):
            continue

async def verified_handle(svc,row,*,close=False):
    from bcc.video_studio.read_verification import open_verified
    root=svc.settings.data_dir/('images' if row['legacy_asset_id'] is not None else 'studio')
    path=ImageStorage(root).resolve_existing(row['file_path'])
    handle=await open_verified(path,row['sha256'],'Studio bytes changed',size=row['file_bytes'])
    if close: handle.close()
    return handle

def public_run(row):
    return {**row,'file_url':f"/api/studio/runs/{row['id']}/file"}
