import base64
import pytest
from pathlib import Path
from bcc.features.images import process_one
from .test_studio_cloud import _png

async def imported(env):
    r=await env.client.post('/api/studio/references',json={'filename':'ref.png','data_base64':base64.b64encode(_png(256,256)).decode()})
    assert r.status_code==200,r.text
    return r.json()

async def test_reference_import_and_invalid_bytes_pair(env):
    row=await imported(env)
    assert row['provenance']['provider']=='owner-import'
    r=await env.client.post('/api/studio/references',json={'filename':'fake.png','data_base64':base64.b64encode(b'not a PNG').decode()})
    assert r.status_code==422
    assert (await env.client.get('/api/studio/runs')).json()['total']==1

async def test_video_import_preserves_provenance_and_revision_gate(env):
    row=await imported(env)
    project=await env.svc.video_studio.store.create('studio-target','Studio target','create-test')
    body={'project_id':project['project_id'],'expected_revision':project['revision'],'operation_id':'studio-import-1'}
    r=await env.client.post(f"/api/studio/runs/{row['id']}/video",json=body)
    assert r.status_code==200,r.text
    assert r.json()['media']['provenance_ref']==row['id']
    stale=await env.client.post(f"/api/studio/runs/{row['id']}/video",json={**body,'operation_id':'studio-import-2'})
    assert stale.status_code==409

async def test_reframe_real_binary_and_unknown_operation_refusal(env):
    row=await imported(env)
    r=await env.client.post(f"/api/studio/runs/{row['id']}/reframe",json={'width':512,'height':256,'mode':'pad'})
    assert r.status_code==200,r.text
    await process_one(env.svc)
    job=(await env.client.get('/api/studio/jobs/'+str(r.json()['id']))).json()
    assert job['status']=='completed',job
    output=(await env.client.get('/api/studio/runs')).json()['items'][0]
    assert output['provenance']['output']['width']==512
    assert output['provenance']['output']['height']==256
    assert output['provenance']['inputs'][0]['sha256']==row['sha256']
    bad=await env.client.post(f"/api/studio/runs/{row['id']}/reframe",json={'width':512,'height':256,'mode':'shell'})
    assert bad.status_code==422

async def test_storyboard_uses_existing_recipe_order(env):
    r=await env.client.post('/api/studio/storyboard',json={'prompt':'Prague launch','model':'mock:image','shots':['hook','reveal','cta']})
    assert r.status_code==200,r.text
    assert len(r.json()['jobs'])==3
    assert len({j['studio']['plane']['collection_id'] for j in r.json()['jobs']})==1
    bad=await env.client.post('/api/studio/storyboard',json={'prompt':'x','model':'mock:image','shots':['invented']})
    assert bad.status_code==422

async def test_package_requires_verified_bytes(env):
    row=await imported(env)
    r=await env.client.post('/api/studio/runs/package',json={'ids':[row['id']]})
    assert r.status_code==200,r.text
    download=await env.client.get(r.json()['download_url'])
    assert download.content[:2]==b'PK'
    Path(row['file_path']).write_bytes(b'tampered')
    bad=await env.client.post('/api/studio/runs/package',json={'ids':[row['id']]})
    assert bad.status_code==409

async def test_agent_gate_rejects_completed_without_local_evidence(env):
    from bcc.studio.integrations import completion_gate
    from bcc.studio.tables import jobs
    from bcc.v2.images_tables import image_jobs
    import sqlalchemy as sa
    from .test_studio_runtime import create
    job=await create(env)
    async with env.svc.db.session() as s:
        await s.execute(sa.update(jobs).where(jobs.c.job_id==job['id']).values(task_id=501,agent_run_id=701))
        await s.execute(sa.update(image_jobs).where(image_jobs.c.id==job['id']).values(status='completed'))
        await s.commit()
    verdict=await completion_gate(env.svc,{'id':501},701,'done')
    assert verdict['verdict']=='FAIL' and verdict['requeue'] is False
    assert (await completion_gate(env.svc,{'id':999},701,'done'))['verdict']=='NOT_APPLICABLE'

async def test_web_designer_uses_existing_edit_and_version_gate(env):
    row=await imported(env)
    p=(await env.client.post('/api/web-designer/projects',json={'name':'Studio website','template':'blank'})).json()
    pid=p['meta']['id']
    edited=await env.client.put(f'/api/web-designer/projects/{pid}/code',json={'html':'<!doctype html><html><body><img src="old.png" alt="hero"></body></html>'})
    version=edited.json()['meta']['version']
    r=await env.client.post(f"/api/studio/runs/{row['id']}/web",json={'project_id':pid,'base_version':version,'path':'html > body > img'})
    assert r.status_code==200,r.text
    code=(await env.client.get(f'/api/web-designer/projects/{pid}')).json()['code']
    assert 'data:image/png;base64,' in code and row['id'] in code
    stale=await env.client.post(f"/api/studio/runs/{row['id']}/web",json={'project_id':pid,'base_version':version,'path':'html > body > img'})
    assert stale.status_code==409

async def test_reference_memory_note_uses_existing_vault_and_rejects_changed_bytes(env,tmp_path):
    row=await imported(env)
    vault=tmp_path/'notes';vault.mkdir()
    configured=await env.client.post('/api/memory/config',json={'root':str(vault),'backend':'local'})
    assert configured.status_code==200,configured.text
    result=await env.client.post('/api/studio/runs/'+row['id']+'/memory',json={'title':'Character reference'})
    assert result.status_code==200,result.text
    note=Path(result.json()['path']).read_text()
    assert row['sha256'] in note and row['id'] in note
    Path(row['file_path']).write_bytes(b'changed')
    rejected=await env.client.post('/api/studio/runs/'+row['id']+'/memory',json={'title':'Changed reference'})
    assert rejected.status_code==409

async def test_generated_bytes_to_timeline_export_and_reopen(env,monkeypatch):
    from .test_studio_cloud import test_real_queue_path_with_stub_http_and_verified_bytes
    from .test_video_studio_integration import create,execute_task,op,BASE
    from bcc.video_studio.store import ProjectStore
    from bcc.db import Database
    from types import SimpleNamespace
    import psutil
    # Admission telemetry is deterministic; provider is stubbed, render/DB/bytes are real.
    monkeypatch.setattr(psutil,'virtual_memory',lambda:SimpleNamespace(total=16*1024**3,available=8*1024**3))
    await test_real_queue_path_with_stub_http_and_verified_bytes(env,monkeypatch)
    row=(await env.client.get('/api/studio/runs')).json()['items'][0]
    p=await create(env);pid=p['id']
    transfer=await env.client.post('/api/studio/runs/'+row['id']+'/video',json={'project_id':pid,'expected_revision':0,'operation_id':op()})
    assert transfer.status_code==200,transfer.text
    media=transfer.json()['media']
    added=await env.client.post(BASE+'/commands',json={'project_id':pid,'expected_revision':1,'operation_id':op(),'command':{'type':'clip.add','track_id':p['sequences'][0]['tracks'][0]['id'],'clip':{'id':'studio-frame','media_id':media['id'],'start':0,'source_in':0,'source_out':400000}}})
    assert added.status_code==200,added.text
    p=added.json()['project']
    exported=await env.client.post(BASE+'/exports',json={'project_id':pid,'expected_revision':p['revision'],'operation_id':op(),'options':{'width':256,'height':256}})
    assert exported.status_code==200,exported.text
    task=await execute_task(env,exported.json()['task_id'])
    assert task['task']['status']=='completed',task
    job=(await env.client.get(BASE+'/exports/'+exported.json()['job_id'])).json()
    assert job['verification']['passed'] and job['verification']['decoded']
    assert (await env.client.get(job['output_url'])).status_code==200
    reopened=Database(env.svc.db.url)
    try:
        saved=await ProjectStore(reopened).get(pid)
        assert saved['media'][media['id']]['provenance_ref']==row['id']
        assert saved['sequences'][0]['tracks'][0]['clips'][0]['id']=='studio-frame'
    finally:await reopened.close()
    Path(row['file_path']).write_bytes(b'tampered')
    assert (await env.client.get(row['file_url'])).status_code==409

async def test_declared_media_surface_matches_container_and_audio_mime(env,tmp_path):
    from bcc.video_studio.media import binary,process
    video=tmp_path/'sample.mp4'
    await process([binary('ffmpeg'),'-v','error','-f','lavfi','-i','color=c=blue:s=256x256:r=10:d=0.2','-c:v','libx264','-pix_fmt','yuv420p',str(video)])
    encoded=base64.b64encode(video.read_bytes()).decode()
    valid=await env.client.post('/api/studio/references',json={'filename':'sample.mp4','data_base64':encoded})
    assert valid.status_code==200,valid.text
    disguised=await env.client.post('/api/studio/references',json={'filename':'pretend.jpg','data_base64':encoded})
    assert disguised.status_code==422
    audio=tmp_path/'sample.mp3'
    await process([binary('ffmpeg'),'-v','error','-f','lavfi','-i','sine=duration=0.2','-c:a','libmp3lame',str(audio)])
    imported=await env.client.post('/api/studio/references',json={'filename':'sample.mp3','data_base64':base64.b64encode(audio.read_bytes()).decode()})
    assert imported.status_code==200,imported.text
    assert imported.json()['mime']=='audio/mpeg'


async def test_agent_generation_goes_through_the_real_permission_decision(env):
    """Агентная генерация не самообслуживается: решает существующий decide_effect.

    Проверяется не поле спецификации, а настоящее решение для настоящего
    зарегистрированного инструмента. Агент без права получает ask — то есть
    у владельца спрашивают, прежде чем задача потратит деньги и запишет файлы.
    Право, выданное владельцем, снимает вопрос: это и есть смысл права.
    Чтение состояния (studio.status) остаётся auto — оно ничего не тратит и
    ничего не пишет, а без него агент не сможет узнать исход.
    """
    from bcc.tools import REGISTRY,decide_effect
    generate=REGISTRY.get('studio.generate')
    status=REGISTRY.get('studio.status')
    assert generate is not None and status is not None,'инструменты Studio не зарегистрированы'
    assert generate.category=='write' and generate.external_output and not generate.idempotent
    args={'model':'mock:image','prompt':'x'}
    effect,reason=decide_effect(generate,args,{'permissions':[]})
    assert effect=='ask',(effect,reason)
    granted,_=decide_effect(generate,args,{'permissions':['filesystem.write']})
    assert granted=='auto'
    read,_=decide_effect(status,{'job_id':1},{'permissions':[]})
    assert read=='auto'


async def test_reframe_survives_the_windows_positional_read_fallback(env,monkeypatch):
    """Рефрейм на Windows падал: копия делалась через дублированный дескриптор.

    `digest_descriptor` читает через `pread`. На POSIX это `os.pread`, и
    смещение дескриптора остаётся нулевым. На Windows позиционного чтения нет,
    и запасной путь двигает общий указатель — после проверки он стоит в конце
    файла. `copy_verified` дублировал этот дескриптор (`os.dup` делит смещение
    с оригиналом), поэтому копировалось ноль байт, и проверка копии честно
    говорила «Copied bytes changed» → RuntimeError. Отсюда `failed` вместо
    `completed` на установленном архиве и зелено на Linux.

    Ветка Windows помечена `pragma: no cover` и живым прогоном не
    проверялась. Здесь она подставляется байт-в-байт, чтобы поведение Windows
    проверялось на Linux-раннере.
    """
    import os,threading
    from bcc.video_studio import media
    seek=threading.Lock()
    def windows_pread(fd,length,offset):
        with seek:
            os.lseek(fd,offset,os.SEEK_SET)
            return os.read(fd,length)
    monkeypatch.setattr(media,'pread',windows_pread)
    row=await imported(env)
    r=await env.client.post(f"/api/studio/runs/{row['id']}/reframe",json={'width':512,'height':256,'mode':'pad'})
    assert r.status_code==200,r.text
    await process_one(env.svc)
    job=(await env.client.get('/api/studio/jobs/'+str(r.json()['id']))).json()
    assert job['status']=='completed',(job['status'],job.get('error'))
    output=(await env.client.get('/api/studio/runs')).json()['items'][0]
    assert output['provenance']['output']['width']==512
    assert output['provenance']['inputs'][0]['sha256']==row['sha256']
