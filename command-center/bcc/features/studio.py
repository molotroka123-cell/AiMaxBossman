"""Authenticated Studio endpoints; the Images feature retains the sole worker."""
from typing import Any
import sqlalchemy as sa
from fastapi import APIRouter,Request,HTTPException
from pydantic import BaseModel,Field,ConfigDict
from bcc.features import Feature
from bcc.db import rows_dicts,utcnow
from bcc.v2.images_tables import image_jobs,image_collections
from bcc.studio.tables import jobs,runs
from bcc.studio import runtime as rt

router=APIRouter(prefix='/studio')
class Strict(BaseModel): model_config=ConfigDict(extra='forbid')
class Media(Strict):
    run_id:str
    role:str
class Job(Strict):
    model:str
    prompt:str=Field(min_length=1,max_length=12000)
    settings:dict[str,Any]=Field(default_factory=dict)
    media:list[Media]=Field(default_factory=list,max_length=16)
    count:int=Field(default=1,ge=1,le=8)
    collection_id:int|None=None
class Patch(Strict):
    favorite:bool|None=None
    collection_id:int|None=None

async def guard(coro):
    try:return await coro
    except rt.StudioError as e:raise HTTPException(409,{'reason':e.reason,'verdict':e.verdict,'message':str(e)}) from None
    except KeyError:raise HTTPException(404,'Studio item not found') from None
    except (RuntimeError,OSError):raise HTTPException(409,'Local bytes missing or changed') from None
    except (ValueError,PermissionError) as e:raise HTTPException(422,str(e)) from None

@router.get('/models')
async def models(request:Request):return {'items':await rt.models(request.app.state.svc)}
@router.post('/jobs')
async def create(body:Job,request:Request):return await guard(rt.create_job(request.app.state.svc,body.model_dump()))
@router.get('/jobs')
async def list_jobs(request:Request):
    svc=request.app.state.svc
    async with svc.db.session() as s:
        ids=list((await s.execute(sa.select(jobs.c.job_id).order_by(jobs.c.job_id.desc()).limit(100))).scalars())
    return {'items':[await rt.get_job(svc,i) for i in ids],'total':len(ids)}
@router.get('/jobs/{jid}')
async def job(jid:int,request:Request):return await guard(rt.get_job(request.app.state.svc,jid))
@router.post('/jobs/{jid}/cancel')
async def cancel(jid:int,request:Request):
    svc=request.app.state.svc
    await guard(rt.get_job(svc,jid))
    async with svc.db.session() as s:
        await s.execute(sa.update(image_jobs).where(image_jobs.c.id==jid,image_jobs.c.status.in_(['queued','running'])).values(status='cancelled',finished_at=utcnow()))
        await s.commit()
    await svc.bus.emit('studio.job.cancelled',job_id=jid)
    return await rt.get_job(svc,jid)
@router.post('/jobs/{jid}/retry')
async def retry(jid:int,request:Request):
    old=await guard(rt.get_job(request.app.state.svc,jid))
    if old['status'] not in ('failed','cancelled'):raise HTTPException(409,'Only stopped jobs may be retried')
    if old['studio']['reason'] in ('interrupted_unknown','owner_stop_provider_unknown'):
        raise HTTPException(409,'Inspect external request before creating a fresh job')
    plane=old['studio']['plane']
    return await guard(rt.create_job(request.app.state.svc,{**plane,'media':[{'run_id':m['run_id'],'role':m['role']} for m in plane['media']]}))
@router.get('/runs')
async def list_runs(request:Request,surface:str='',favorite:bool=False,deleted:bool=False,collection_id:int|None=None,q:str='',limit:int=100,offset:int=0,job_id:int|None=None):
    svc=request.app.state.svc
    await rt.migrate_legacy(svc)
    clause=[runs.c.deleted==deleted]
    if surface:clause.append(runs.c.surface==surface)
    if favorite:clause.append(runs.c.favorite.is_(True))
    if collection_id is not None:clause.append(runs.c.collection_id==collection_id)
    if job_id is not None:clause.append(runs.c.job_id==job_id)
    if q:clause.append(sa.cast(runs.c.provenance,sa.Text).contains(q[:200],autoescape=True))
    async with svc.db.session() as s:
        total=(await s.execute(sa.select(sa.func.count()).select_from(runs).where(*clause))).scalar_one()
        rows=rows_dicts((await s.execute(sa.select(runs).where(*clause).order_by(runs.c.created_at.desc(),runs.c.id).limit(min(max(limit,1),200)).offset(max(offset,0)))).all())
    return {'items':[rt.public_run(r) for r in rows],'total':total}
async def run(svc,rid):
    row=await rt.one(svc,runs,runs.c.id,rid)
    if not row:raise HTTPException(404,'Studio run not found')
    return row
@router.get('/runs/{rid}')
async def get_run(rid:str,request:Request):return rt.public_run(await run(request.app.state.svc,rid))
@router.get('/runs/{rid}/file')
async def file(rid:str,request:Request):
    from bcc.video_studio.descriptor_stream import stream_verified
    svc=request.app.state.svc;row=await run(svc,rid)
    if row['deleted']:raise HTTPException(404,'Run is in trash')
    try:handle=await rt.verified_handle(svc,row)
    except (ValueError,RuntimeError,OSError):raise HTTPException(409,'Output bytes missing or changed') from None
    return stream_verified(handle,request,media_type=row['mime'])
@router.patch('/runs/{rid}')
async def patch(rid:str,body:Patch,request:Request):
    svc=request.app.state.svc;await run(svc,rid)
    changes=body.model_dump(exclude_unset=True)
    if 'favorite' in changes and changes['favorite'] is None:raise HTTPException(422,'favorite must be boolean')
    if changes.get('collection_id') is not None and not await rt.one(svc,image_collections,image_collections.c.id,changes['collection_id']):raise HTTPException(422,'collection_id missing')
    if changes:
        async with svc.db.session() as s:
            await s.execute(sa.update(runs).where(runs.c.id==rid).values(**changes));await s.commit()
    return rt.public_run(await run(svc,rid))
@router.delete('/runs/{rid}')
async def trash(rid:str,request:Request):return await set_deleted(request.app.state.svc,rid,True)
@router.post('/runs/{rid}/restore')
async def restore(rid:str,request:Request):return await set_deleted(request.app.state.svc,rid,False)
async def set_deleted(svc,rid,deleted):
    await run(svc,rid)
    async with svc.db.session() as s:
        await s.execute(sa.update(runs).where(runs.c.id==rid).values(deleted=deleted));await s.commit()
    return rt.public_run(await run(svc,rid))
@router.post('/runs/{rid}/reuse')
async def reuse(rid:str,request:Request):
    row=await run(request.app.state.svc,rid)
    return {'plane':row['provenance']['plane']}
FEATURE=Feature(name='studio',router=router,setup=rt.setup)

class Policy(Strict):
    enabled:bool=False
    free_only:bool=True
    cloud_budget_usd:float=Field(default=0,ge=0,allow_inf_nan=False)
    per_job_usd:float=Field(default=0,ge=0,allow_inf_nan=False)
    prices:dict[str,float]=Field(default_factory=dict)
    download_hosts:list[str]=Field(default_factory=list)
@router.get('/policy')
async def get_policy(request:Request):
    from bcc.studio.governance import policy
    return await policy(request.app.state.svc)
@router.put('/policy')
async def put_policy(body:Policy,request:Request):
    from bcc.studio.governance import save_policy
    return await guard(save_policy(request.app.state.svc,body.model_dump()))
@router.get('/budget')
async def get_budget(request:Request):
    from bcc.studio.governance import budget_status
    return await budget_status(request.app.state.svc)
class Consent(Strict):
    provider:str='openrouter'
    media:list[Media]=Field(min_length=1,max_length=16)
@router.post('/egress/confirm')
async def consent(body:Consent,request:Request):
    from bcc.studio.governance import confirm
    inputs=[];svc=request.app.state.svc
    for media in body.media:
        row=await run(svc,media.run_id)
        if row['deleted']:raise HTTPException(422,'Reference is deleted')
        await guard(rt.verified_handle(svc,row,close=True))
        inputs.append({'run_id':row['id'],'role':media.role,'sha256':row['sha256']})
    return {'confirmation':await guard(confirm(svc,body.provider,inputs))}
@router.delete('/egress/confirmations')
async def revoke(request:Request):
    from bcc.studio.tables import config
    async with request.app.state.svc.db.session() as s:
        await s.execute(sa.delete(config).where(config.c.key.startswith('egress:')));await s.commit()
    return {'revoked':True}

class Reference(Strict):
    filename:str=Field(min_length=1,max_length=240)
    data_base64:str=Field(max_length=21*1024*1024)
@router.post('/references')
async def reference(body:Reference,request:Request):
    from bcc.studio.integrations import import_reference
    return await guard(import_reference(request.app.state.svc,body.filename,body.data_base64))
class VideoTransfer(Strict):
    project_id:str
    expected_revision:int=Field(ge=0)
    operation_id:str=Field(min_length=1,max_length=96)
@router.post('/runs/{rid}/video')
async def video(rid:str,body:VideoTransfer,request:Request):
    from bcc.studio.integrations import into_video
    from bcc.features.video_studio import guarded
    svc=request.app.state.svc
    return await guarded(into_video(svc,await run(svc,rid),body.model_dump()))
class Reframe(Strict):
    width:int=Field(ge=256,le=4096,multiple_of=2)
    height:int=Field(ge=256,le=4096,multiple_of=2)
    mode:str=Field(default='pad',pattern='^(pad|crop)$')
@router.post('/runs/{rid}/reframe')
async def reframe(rid:str,body:Reframe,request:Request):
    from bcc.studio.integrations import reframe as apply
    svc=request.app.state.svc
    return await guard(apply(svc,await run(svc,rid),**body.model_dump()))
class Storyboard(Strict):
    prompt:str=Field(min_length=1,max_length=8000)
    model:str
    settings:dict[str,Any]=Field(default_factory=dict)
    shots:list[str]=Field(default_factory=lambda:['hook','reveal','detail','benefit','cta'],min_length=1,max_length=12)
@router.post('/storyboard')
async def storyboard(body:Storyboard,request:Request):
    from bcc.studio.integrations import storyboard as plan
    return await guard(plan(request.app.state.svc,body.model_dump()))
class Package(Strict):ids:list[str]=Field(min_length=1,max_length=100)
@router.post('/runs/package')
async def package(body:Package,request:Request):
    from bcc.studio.integrations import package as pack
    try:return await guard(pack(request.app.state.svc,body.ids))
    except (RuntimeError,OSError):raise HTTPException(409,'Output bytes missing or changed') from None
@router.get('/packages/{pid}')
async def package_file(pid:str,request:Request):
    from bcc.studio.tables import config
    from bcc.video_studio.read_verification import open_verified
    from bcc.video_studio.descriptor_stream import stream_verified
    svc=request.app.state.svc;record=await rt.one(svc,config,config.c.key,'package:'+pid)
    if not record:raise HTTPException(404,'Package not found')
    info=record['value']
    try:handle=await open_verified(rt.storage(svc).resolve_existing(info['path']),info['sha256'],'Package changed',size=info['bytes'])
    except (ValueError,OSError,RuntimeError):raise HTTPException(409,'Package bytes changed') from None
    return stream_verified(handle,request,media_type='application/zip',filename='studio-results.zip')

class WebTransfer(Strict):
    project_id:int
    base_version:int=Field(ge=1)
    path:str=Field(min_length=1,max_length=1000)
@router.post('/runs/{rid}/web')
async def web(rid:str,body:WebTransfer,request:Request):
    import base64,os
    from bcc.features.web_designer import EditIn,edit_project
    svc=request.app.state.svc;row=await run(svc,rid)
    if row['deleted'] or row['surface']!='image' or row['mime']=='image/svg+xml' or row['file_bytes']>1024*1024:raise HTTPException(422,'Choose a raster image below 1 MiB for a self-contained website')
    try:handle=await rt.verified_handle(svc,row)
    except (ValueError,RuntimeError,OSError):raise HTTPException(409,'Output changed') from None
    # Positional read from offset 0, never a plain os.read: after
    # digest_descriptor the Windows pread fallback leaves the shared file
    # pointer at EOF, and a sequential read returned zero bytes there — a
    # 409 for a file that never changed.
    from bcc.video_studio.media import pread
    try:
        data=b'';limit=row['file_bytes']+1
        while len(data)<limit:
            block=pread(handle.descriptor,limit-len(data),len(data))
            if not block:break
            data+=block
    finally:handle.close()
    import hashlib
    if hashlib.sha256(data).hexdigest()!=row['sha256']:raise HTTPException(409,'Output changed during read')
    return await edit_project(body.project_id,EditIn(op='attrs',path=body.path,tag='img',base_version=body.base_version,attrs={'src':f"data:{row['mime']};base64,"+base64.b64encode(data).decode(),'data-studio-provenance':rid}),request)

@router.get('/capabilities')
async def capabilities(request:Request):
    import shutil
    return {'reframe':{'BUNDLED':True,'CONFIGURED':bool(shutil.which('ffmpeg')),'MODEL_REQUIRED':False,'EXTERNAL_SERVICE_REQUIRED':False,'VERIFIED':False},
        'higgsfield':{'status':'OWNER_REQUIRED','adoption':'REFERENCE_ONLY','reason':'Official MCP acceptance not supplied; REST contract not supplied'},
        'tts':{'BUNDLED':False,'CONFIGURED':False,'MODEL_REQUIRED':True,'EXTERNAL_SERVICE_REQUIRED':True,'VERIFIED':False,'status':'OWNER_REQUIRED'},
        'upscale':{'BUNDLED':False,'CONFIGURED':False,'MODEL_REQUIRED':True,'EXTERNAL_SERVICE_REQUIRED':True,'VERIFIED':False,'status':'OWNER_REQUIRED'},
        'background_removal':{'BUNDLED':False,'CONFIGURED':False,'MODEL_REQUIRED':True,'EXTERNAL_SERVICE_REQUIRED':True,'VERIFIED':False,'status':'OWNER_REQUIRED'}}
@router.post('/models/refresh')
async def refresh(request:Request):
    return {'status':'OWNER_REQUIRED','changed':False,'reason':'No accepted official model-schema connector is configured; local declarations revalidated','items':await rt.models(request.app.state.svc)}
@router.get('/models/recommend')
async def recommend(request:Request,surface:str='image'):
    items=await rt.models(request.app.state.svc)
    return {'items':[m for m in items if m['surface']==surface and m['verified'] and m['available'] and m['provider']=='comfyui'], 'reason':'Only locally verified configured generation is suggested. No paid fallback.'}

class ReferenceNote(Strict):
    title:str=Field(min_length=1,max_length=120,pattern=r'^[^\r\n]+$')
@router.post('/runs/{rid}/memory')
async def memory_reference(rid:str,body:ReferenceNote,request:Request):
    from bcc.features.tools_memory import get_service,MemoryNotConfigured
    from bcc.oss.qdrant import QdrantUnavailable
    svc=request.app.state.svc;row=await run(svc,rid)
    if row['deleted']:raise HTTPException(409,'Reference is in trash')
    await guard(rt.verified_handle(svc,row,close=True))
    try:service=await get_service(svc)
    except (MemoryNotConfigured,FileNotFoundError):raise HTTPException(409,{'verdict':'OWNER_REQUIRED','reason':'Configure the existing notes vault first'}) from None
    content=f"Studio reference: {rid}\nSHA-256: {row['sha256']}\nSurface: {row['surface']}\nProvider: {row['provenance']['provider']}\nLocal gallery: /#/images?studio=1\n\nThis is an owner reference, not a trained identity or Soul ID."
    try:path=await service.remember(title=body.title,content=content,kind='note',project='Studio references',tags=['studio','reference'],filename='studio-reference-'+rid+'.md')
    except FileExistsError:raise HTTPException(409,'A note for this reference already exists') from None
    except QdrantUnavailable:raise HTTPException(503,{'saved':True,'reason':'Reference note saved; memory index unavailable'}) from None
    return {'path':str(path),'run_id':rid,'sha256':row['sha256']}
