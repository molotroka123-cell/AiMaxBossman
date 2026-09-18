"""Consume verified Studio bytes through the existing editor and agent boundaries."""
import asyncio
import base64
import json
import os
import shutil
import zipfile
from pathlib import Path
from uuid import uuid4
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert
from bcc.studio import runtime as rt
from bcc.studio.tables import runs,jobs,config
from bcc.v2.images_tables import image_jobs,image_collections

async def copy_verified(svc,row,target):
    handle=await rt.verified_handle(svc,row)
    try:
        target.parent.mkdir(parents=True,exist_ok=True)
        def copy():
            with os.fdopen(os.dup(handle.descriptor),'rb') as source,target.open('xb') as out:shutil.copyfileobj(source,out,256*1024)
        await asyncio.to_thread(copy)
        from bcc.video_studio.media import digest_file
        if await asyncio.to_thread(digest_file,target)!=row['sha256']:raise RuntimeError('Copied bytes changed')
    finally:handle.close()
    return target

async def import_reference(svc,filename,encoded):
    suffix=Path(filename).suffix.lower()
    if suffix not in ('.png','.jpg','.jpeg','.mp4','.wav','.mp3'):raise ValueError('filename: unsupported raster/video/audio type')
    data=base64.b64decode(encoded,validate=True)
    if not 0<len(data)<=15*1024*1024:raise ValueError('reference exceeds 15 MiB or is empty')
    surface='video' if suffix=='.mp4' else 'audio' if suffix in ('.wav','.mp3') else 'image'
    path=rt.storage(svc).save('references/'+uuid4().hex+suffix,data)
    try:
        rid=await rt.persist(svc,None,{'model':'owner-import','prompt':Path(filename).name,'settings':{},'media':[],'count':1,'collection_id':None},{'provider':'owner-import','id':'owner-import','surface':surface},path,cost=0)
    except BaseException:path.unlink(missing_ok=True);raise
    return rt.public_run(await rt.one(svc,runs,runs.c.id,rid))

async def into_video(svc,row,payload):
    if row['deleted']:raise ValueError('result is in trash')
    temp=rt.storage(svc).root/('transfer-'+uuid4().hex)
    try:
        await copy_verified(svc,row,temp)
        media=await svc.video_studio.media.import_file(temp,name=row['id']+Path(row['file_path']).suffix)
        media['provenance_ref']=row['id']
        result=await svc.video_studio.command({**payload,'command':{'type':'media.import','media':media}},trusted_media=True)
        return {**result,'media':media}
    finally:temp.unlink(missing_ok=True)

async def reframe(svc,row,width,height,mode):
    if row['surface'] not in ('image','video') or row['mime']=='image/svg+xml':raise ValueError('reframe supports raster images/video, not demo SVG')
    return await rt.create_job(svc,{'model':'local:reframe','prompt':row['provenance']['plane']['prompt']+' / '+mode,'settings':{'width':width,'height':height,'mode':mode},'media':[{'run_id':row['id'],'role':'reference'}]})

async def process_reframe(svc,job,ext,model):
    from bcc.video_studio.media import binary,input_args,process
    plane=ext['plane'];source=await rt.one(svc,runs,runs.c.id,plane['media'][0]['run_id'])
    temp=rt.storage(svc).root/('reframe-source-'+uuid4().hex)
    output=rt.storage(svc).root/('reframe-'+uuid4().hex+('.png' if source['surface']=='image' else '.mp4'))
    w,h=plane['settings']['width'],plane['settings']['height'];mode=plane['settings']['mode']
    filter=f'scale={w}:{h}:force_original_aspect_ratio='+('increase' if mode=='crop' else 'decrease')
    filter+=f',crop={w}:{h}' if mode=='crop' else f',pad={w}:{h}:(ow-iw)/2:(oh-ih)/2'
    try:
        await copy_verified(svc,source,temp)
        args=[binary('ffmpeg'),'-v','error','-y',*input_args(temp),'-vf',filter]
        args+=['-frames:v','1'] if source['surface']=='image' else ['-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac']
        await process([*args,str(output)],timeout=120)
        rid=await rt.persist(svc,job['id'],plane,{**model,'surface':source['surface']},output,cost=0)
        if rid is None:output.unlink(missing_ok=True)
    except BaseException:output.unlink(missing_ok=True);raise
    finally:temp.unlink(missing_ok=True)

async def storyboard(svc,payload):
    from bcc.video_studio.storyboard import RECIPES
    shots=payload['shots']
    if any(x not in RECIPES for x in shots):raise ValueError('shots: unknown recipe')
    # This is a generation plan using canonical recipes, NOT a montage with fabricated media ids.
    prepared=[]
    for index,shot in enumerate(shots):
        p={'model':payload['model'],'prompt':payload['prompt']+' — '+RECIPES[shot].purpose,'settings':payload.get('settings',{}),'count':1}
        await rt.validate_plane(svc,p);prepared.append(p)
    async with svc.db.session() as s:
        result=await s.execute(sa.insert(image_collections).values(name='Studio '+uuid4().hex[:12]));cid=int(result.inserted_primary_key[0]);await s.commit()
    output=[]
    for index,p in enumerate(prepared):
        job=await rt.create_job(svc,{**p,'collection_id':cid})
        async with svc.db.session() as s:
            await s.execute(sa.update(image_jobs).where(image_jobs.c.id==job['id']).values(options={'studio':True,'storyboard':{'recipe':shots[index],'index':index,'collection_id':cid}}));await s.commit()
        output.append(job)
    return {'collection_id':cid,'jobs':output,'kind':'generation_plan','recipes':shots}

async def package(svc,ids):
    paths=[];manifest=[];temporary=rt.storage(svc).root/('package-source-'+uuid4().hex)
    target=rt.storage(svc).root/('package-'+uuid4().hex+'.zip')
    try:
        total=0
        for rid in dict.fromkeys(ids):
            row=await rt.one(svc,runs,runs.c.id,rid)
            if not row or row['deleted']:raise ValueError('package: result missing/deleted')
            total+=row['file_bytes']
            if total>256*1024*1024:raise ValueError('package exceeds 256 MiB')
            path=temporary/(rid+Path(row['file_path']).suffix)
            await copy_verified(svc,row,path);paths.append(path);manifest.append(row['provenance'])
        def write():
            with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_STORED) as z:
                for path in paths:z.write(path,path.name)
                z.writestr('provenance.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        await asyncio.to_thread(write)
        from bcc.video_studio.media import digest_file
        pid=uuid4().hex
        info={'path':str(target),'sha256':await asyncio.to_thread(digest_file,target),'bytes':target.stat().st_size}
        async with svc.db.session() as s:
            await s.execute(sa.insert(config).values(key='package:'+pid,value=info));await s.commit()
        return {'download_url':'/api/studio/packages/'+pid}
    finally:shutil.rmtree(temporary,ignore_errors=True)

async def completion_gate(svc,task,run_id,answer):
    async with svc.db.session() as s:
        ids=list((await s.execute(sa.select(jobs.c.job_id).where(jobs.c.task_id==task['id'],jobs.c.agent_run_id==run_id))).scalars())
    if not ids:return {'verdict':'NOT_APPLICABLE'}
    for jid in ids:
        job=await rt.get_job(svc,jid)
        async with svc.db.session() as s:items=[dict(r._mapping) for r in (await s.execute(sa.select(runs).where(runs.c.job_id==jid))).all()]
        if job['status']!='completed' or not items or any(r['provenance']['mock'] for r in items):
            return {'verdict':'FAIL','requeue':False,'status':'failed','reasons':'studio: pending/demo/missing verified bytes'}
        try:
            for row in items:await rt.verified_handle(svc,row,close=True)
        except (ValueError,OSError,RuntimeError):return {'verdict':'FAIL','requeue':False,'status':'failed','reasons':'studio: output bytes changed'}
    return {'verdict':'PASS'}

async def tool_generate(args,ctx):
    from bcc.features.studio import Job
    from bcc.tools import ToolResult
    payload=Job.model_validate(args).model_dump()
    job=await rt.create_job(ctx.svc,payload)
    async with ctx.svc.db.session() as s:
        await s.execute(sa.update(jobs).where(jobs.c.job_id==job['id']).values(task_id=ctx.task['id'],agent_run_id=ctx.run_id));await s.commit()
    value={'job_id':job['id'],'status':'queued','evidence':False}
    return ToolResult(content=json.dumps(value),data=value,one_line='Studio queued; not completed',external=True)

async def tool_status(args,ctx):
    from bcc.tools import ToolResult
    job=await rt.get_job(ctx.svc,args['job_id'])
    if job['studio']['task_id']!=ctx.task['id'] or job['studio']['agent_run_id']!=ctx.run_id:raise PermissionError('job belongs to another task/run')
    result={'job_id':job['id'],'status':job['status'],'reason':job['studio']['reason'],'verdict':job['studio']['verdict']}
    return ToolResult(content=json.dumps(result),data=result,external=True)

def register(svc):
    from bcc.tools import REGISTRY,ToolSpec
    REGISTRY.register(ToolSpec(name='studio.generate',description='Queue generation; use studio.status. A queued/remote-completed job is not evidence.',handler=tool_generate,input_schema={'model':{'type':'string'},'prompt':{'type':'string'},'settings':{'type':'object'},'media':{'type':'array'},'count':{'type':'integer'}},required=['model','prompt'],category='write',permission='filesystem.write',source='studio',default_effect='ask',idempotent=False,external_output=True))
    REGISTRY.register(ToolSpec(name='studio.status',description='Inspect this task generation job.',handler=tool_status,input_schema={'job_id':{'type':'integer'}},required=['job_id'],category='read',source='studio',default_effect='auto',external_output=True))
    async def gate(task,run_id,answer):return await completion_gate(svc,task,run_id,answer)
    svc.engine.add_hook('gate_completion',gate)
