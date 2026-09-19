"""Cloud guard tests use an injected HTTP transport, never a live provider."""
import asyncio
import base64
import hashlib
import httpx
import pytest
from bcc.features.images import process_one
from .test_studio_runtime import create
def _png(w,h):
    import struct,zlib
    def chunk(k,d):return struct.pack('>I',len(d))+k+d+struct.pack('>I',zlib.crc32(k+d)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\0'*w*3)*h))+chunk(b'IEND',b'')

async def test_default_cloud_disabled_never_dispatches(env):
    job=await create(env,model='openrouter:google/gemini-3.1-flash-image',settings={'aspect_ratio':'16:9'})
    await process_one(env.svc)
    row=(await env.client.get(f"/api/studio/jobs/{job['id']}")).json()
    assert row['studio']['verdict']=='OWNER_REQUIRED'
    assert row['studio']['reason']=='unauthorized'

@pytest.mark.parametrize('reason,status', [('unauthorized',401),('insufficient_credit',402),('throttled',429),('provider_down',404)])
async def test_openrouter_http_reasons_preserved(reason,status):
    from bcc.studio.providers.openrouter import OpenRouterProvider
    from bcc.studio.provider import GenerationPlane,ProviderFailure
    p=OpenRouterProvider('test-secret',transport=httpx.MockTransport(lambda r:httpx.Response(status,json={})),allowed_download_hosts=[])
    with pytest.raises(ProviderFailure) as caught:await p.submit(GenerationPlane('openrouter:google/gemini-3.1-flash-image','test'))
    assert caught.value.status.reason==reason
    assert 'test-secret' not in str(caught.value)

async def test_image_adapter_submit_fetch_and_corrupt_rejection(tmp_path):
    from bcc.studio.providers.openrouter import OpenRouterProvider
    from bcc.studio.provider import GenerationPlane,ProviderFailure
    png=_png(256,256)
    def serve(r):
        assert r.headers['Authorization']=='Bearer test-secret'
        return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(png).decode()}],'usage':{'cost':0}})
    p=OpenRouterProvider('test-secret',transport=httpx.MockTransport(serve),allowed_download_hosts=[])
    receipt=await p.submit(GenerationPlane('openrouter:google/gemini-3.1-flash-image','test'))
    status=await p.status(receipt.request_id)
    assert status.state=='completed' and not status.is_evidence
    out=await p.fetch(status.outputs[0],tmp_path/'result.png')
    assert out.sha256==hashlib.sha256(png).hexdigest()

async def test_video_cdn_never_receives_key_and_redirect_refused(tmp_path):
    from bcc.studio.providers.openrouter import OpenRouterProvider
    from bcc.studio.provider import GenerationPlane,ProviderFailure
    seen=[]
    def serve(r):
        seen.append((r.url.host,dict(r.headers)))
        if r.url.host=='openrouter.ai' and r.method=='POST':return httpx.Response(202,json={'id':'vid-1'})
        if r.url.host=='openrouter.ai':return httpx.Response(200,json={'status':'completed','unsigned_urls':['https://cdn.example.test/out.mp4']})
        return httpx.Response(302,headers={'Location':'http://169.254.169.254/latest/meta-data'})
    p=OpenRouterProvider('test-secret',transport=httpx.MockTransport(serve),allowed_download_hosts=['cdn.example.test'])
    receipt=await p.submit(GenerationPlane('openrouter:minimax/hailuo-3-max','test'))
    status=await p.status(receipt.request_id)
    with pytest.raises(ProviderFailure):await p.fetch(status.outputs[0],tmp_path/'result.mp4')
    assert 'authorization' not in seen[-1][1]
    assert not (tmp_path/'result.mp4').exists()

@pytest.mark.parametrize('url',['http://cdn.example.test/x','https://127.0.0.1/x','https://evil.test/x','https://u:secret@cdn.example.test/x'])
async def test_download_url_not_in_allowlist_refused(tmp_path,url):
    from bcc.studio.providers.openrouter import OpenRouterProvider
    from bcc.studio.provider import GenerationPlane,ProviderFailure
    calls=[]
    def serve(r):
        calls.append(r.url.host)
        return httpx.Response(202,json={'id':'v'}) if r.method=='POST' else httpx.Response(200,json={'status':'completed','unsigned_urls':[url]})
    p=OpenRouterProvider('test-secret',transport=httpx.MockTransport(serve),allowed_download_hosts=['cdn.example.test'])
    rec=await p.submit(GenerationPlane('openrouter:minimax/hailuo-3-max','t'))
    status=await p.status(rec.request_id)
    with pytest.raises(ProviderFailure):await p.fetch(status.outputs[0],tmp_path/'bad')
    assert calls==['openrouter.ai','openrouter.ai']

async def test_unknown_price_free_only_and_budget_stop_before_dispatch(env):
    from bcc.studio.governance import save_policy,reserve
    from bcc.studio.runtime import StudioError
    model='openrouter:minimax/hailuo-3-max'
    await save_policy(env.svc,{'enabled':True,'free_only':True,'cloud_budget_usd':1,'per_job_usd':1,'prices':{}})
    with pytest.raises(StudioError,match='price'):await reserve(env.svc,model,1,1,[])
    await save_policy(env.svc,{'enabled':True,'free_only':True,'cloud_budget_usd':1,'per_job_usd':1,'prices':{model:0.1}})
    with pytest.raises(StudioError,match='free_only'):await reserve(env.svc,model,1,1,[])
    await save_policy(env.svc,{'enabled':True,'free_only':False,'cloud_budget_usd':0.1,'per_job_usd':1,'prices':{model:0.2}})
    with pytest.raises(StudioError,match='budget'):await reserve(env.svc,model,1,1,[])

async def test_egress_requires_digest_bound_confirmation(env):
    from bcc.studio.governance import save_policy,reserve,confirm
    from bcc.studio.runtime import StudioError
    model='openrouter:google/gemini-3.1-flash-image'
    # BL-084: платная модель проходит не по нулю при free_only, а по названной цене.
    policy={'enabled':True,'free_only':False,'cloud_budget_usd':1,'per_job_usd':1,'prices':{model:0.01}}
    await save_policy(env.svc,policy)
    inputs=[{'run_id':'one','role':'reference','sha256':'a'*64}]
    with pytest.raises(StudioError,match='egress'):await reserve(env.svc,model,1,1,inputs)
    await confirm(env.svc,'openrouter',inputs)
    assert (await reserve(env.svc,model,1,1,inputs))['upper_bound_usd']==0.01
    changed=[{**inputs[0],'sha256':'b'*64}]
    with pytest.raises(StudioError,match='egress'):await reserve(env.svc,model,2,1,changed)

async def test_real_queue_path_with_stub_http_and_verified_bytes(env,monkeypatch):
    from bcc.studio.providers import openrouter
    from bcc.studio.governance import save_policy
    cls=openrouter.OpenRouterProvider
    requests=[]
    def serve(r):
        requests.append(r.url.path)
        if r.url.path.endswith('/auth/key'):return httpx.Response(200,json={'data':{'limit_remaining':1}})
        return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(_png(256,256)).decode()}],'usage':{'cost':0}})
    monkeypatch.setattr(openrouter,'OpenRouterProvider',lambda key,**kw:cls(key,transport=httpx.MockTransport(serve),**kw))
    monkeypatch.setenv('OPENROUTER_API_KEY','test-only-key')
    model='openrouter:google/gemini-3.1-flash-image'
    # BL-084: то же — честная цена вместо нуля, который каталог не подтверждает.
    await save_policy(env.svc,{'enabled':True,'free_only':False,'cloud_budget_usd':1,'per_job_usd':1,'prices':{model:0.01}})
    job=await create(env,model=model,settings={'aspect_ratio':'16:9'})
    await process_one(env.svc)
    row=(await env.client.get('/api/studio/jobs/'+str(job['id']))).json()
    assert row['status']=='completed',row
    runs=(await env.client.get('/api/studio/runs')).json()['items']
    assert len(runs)==1 and runs[0]['provenance']['cost_usd']==0
    assert runs[0]['provenance']['provider_request_id']=='NOT_CAPTURED:provider_did_not_return_id'
    assert requests==['/api/v1/auth/key','/api/v1/images']
    assert not any(m['verified'] for m in (await env.client.get('/api/studio/models')).json()['items'])

async def test_cancellation_during_video_submit_releases_worker(env,monkeypatch):
    from bcc.studio.providers import openrouter
    from bcc.studio.governance import save_policy
    cls=openrouter.OpenRouterProvider;entered=asyncio.Event();cancelled=asyncio.Event()
    async def serve(r):
        if r.url.path.endswith('/auth/key'):return httpx.Response(200,json={'data':{'limit_remaining':1}})
        entered.set()
        try:await asyncio.Event().wait()
        finally:cancelled.set()
    monkeypatch.setattr(openrouter,'OpenRouterProvider',lambda key,**kw:cls(key,transport=httpx.MockTransport(serve),**kw))
    monkeypatch.setenv('OPENROUTER_API_KEY','test-only-key')
    model='openrouter:minimax/hailuo-3-max'
    # BL-084: free_only по умолчанию True, а модель в каталоге платная — цену называем честно.
    await save_policy(env.svc,{'enabled':True,'free_only':False,'cloud_budget_usd':1,'per_job_usd':1,'prices':{model:0.01}})
    job=await create(env,model=model,settings={})
    worker=asyncio.create_task(process_one(env.svc))
    await asyncio.wait_for(entered.wait(),2)
    await env.client.post('/api/studio/jobs/'+str(job['id'])+'/cancel')
    await asyncio.wait_for(worker,2)
    assert cancelled.is_set()
    assert (await env.client.get('/api/studio/runs')).json()['total']==0
    next_job=await create(env)
    assert await process_one(env.svc)==next_job['id']

async def test_empty_completed_dispatch_cannot_complete_job(env,monkeypatch):
    from bcc.studio import dispatch
    async def empty(*args):pass
    monkeypatch.setattr(dispatch,'generate',empty)
    job=await create(env,model='openrouter:google/gemini-3.1-flash-image',settings={})
    await process_one(env.svc)
    row=(await env.client.get('/api/studio/jobs/'+str(job['id']))).json()
    assert row['status']=='failed'
    assert row['studio']['reason']=='malformed'

async def test_policy_and_consent_revocation_stop_admitted_requests(env):
    from bcc.studio.governance import save_policy,reserve,confirm,check_current
    from bcc.studio.runtime import StudioError
    model='openrouter:google/gemini-3.1-flash-image'
    # BL-084: free_only по умолчанию True, а модель в каталоге платная — цену называем честно.
    p={'enabled':True,'free_only':False,'cloud_budget_usd':1,'per_job_usd':1,'prices':{model:0.01}}
    await save_policy(env.svc,p)
    media=[{'run_id':'r','role':'reference','sha256':'a'*64}]
    await confirm(env.svc,'openrouter',media)
    admitted=await reserve(env.svc,model,1,1,media)
    await check_current(env.svc,admitted)
    await env.client.delete('/api/studio/egress/confirmations')
    with pytest.raises(StudioError,match='revoked'):await check_current(env.svc,admitted)
    await confirm(env.svc,'openrouter',media)
    await save_policy(env.svc,{**p,'enabled':False})
    with pytest.raises(StudioError,match='changed'):await check_current(env.svc,admitted)

async def test_verification_is_bound_to_current_provider_configuration(env,monkeypatch):
    from bcc.studio.runtime import provider_fingerprint
    from bcc.studio.tables import config
    from bcc.studio.governance import save_policy
    from datetime import datetime,timezone
    import sqlalchemy as sa
    await test_real_queue_path_with_stub_http_and_verified_bytes(env,monkeypatch)
    row=(await env.client.get('/api/studio/runs')).json()['items'][0]
    model=row['model']
    proof={'run_id':row['id'],'model':model,'at':datetime.now(timezone.utc).isoformat(),'configuration':await provider_fingerprint(env.svc,model)}
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(config).values(key='probe:'+model,value=proof));await s.commit()
    assert next(m for m in (await env.client.get('/api/studio/models')).json()['items'] if m['id']==model)['verified']
    monkeypatch.setenv('OPENROUTER_API_KEY','changed-test-only-key')
    assert not next(m for m in (await env.client.get('/api/studio/models')).json()['items'] if m['id']==model)['verified']

async def test_video_completion_downloads_decodes_and_keeps_key_off_cdn(env,monkeypatch,tmp_path):
    from bcc.studio.providers import openrouter
    from bcc.studio.governance import save_policy
    from bcc.video_studio.media import binary,process
    path=tmp_path/'video.mp4'
    await process([binary('ffmpeg'),'-v','error','-f','lavfi','-i','color=c=blue:s=256x256:r=10:d=0.2','-c:v','libx264','-pix_fmt','yuv420p',str(path)])
    cls=openrouter.OpenRouterProvider;seen=[]
    def serve(r):
        seen.append((r.method,r.url.host,r.url.path))
        if r.url.host=='cdn.example.test':
            assert 'authorization' not in r.headers
            return httpx.Response(200,content=path.read_bytes())
        if r.url.path.endswith('/auth/key'):return httpx.Response(200,json={'data':{'limit_remaining':1}})
        if r.method=='POST':return httpx.Response(202,json={'id':'video-proof'})
        return httpx.Response(200,json={'status':'completed','unsigned_urls':['https://cdn.example.test/proof.mp4'],'usage':{'cost':0}})
    monkeypatch.setattr(openrouter,'OpenRouterProvider',lambda key,**kw:cls(key,transport=httpx.MockTransport(serve),**kw))
    monkeypatch.setenv('OPENROUTER_API_KEY','test-only-key')
    model='openrouter:minimax/hailuo-3-max'
    # BL-084: free_only по умолчанию True, а модель в каталоге платная — цену называем честно.
    await save_policy(env.svc,{'enabled':True,'free_only':False,'cloud_budget_usd':1,'per_job_usd':1,'prices':{model:0.01},'download_hosts':['cdn.example.test']})
    job=await create(env,model=model,settings={})
    await process_one(env.svc)
    row=(await env.client.get('/api/studio/jobs/'+str(job['id']))).json()
    assert row['status']=='completed',row
    run=(await env.client.get('/api/studio/runs')).json()['items'][0]
    assert run['surface']=='video' and run['provenance']['output']['duration_ms']>0
    assert run['provenance']['provider_request_id']=='video-proof'
    assert len(seen)==4
    assert (await env.client.get(run['file_url'])).content==path.read_bytes()

async def test_cap_refuses_the_next_reservation_and_cancellation_returns_nothing(env,monkeypatch):
    """Потолок режет следующую заявку, а отмена не возвращает зарезервированное.

    Невозврат — не забытая доработка, а решение: заявка уже ушла провайдеру, и
    он вправе списать деньги независимо от того, дождались мы ответа или нет.
    Вернуть резерв на отмене значило бы разрешить обойти дневной предел
    циклом «поставил — отменил». Проверяется именно та отмена, что рвёт
    отправку посреди запроса, а не отмена задачи в очереди.
    """
    from bcc.studio.providers import openrouter
    from bcc.studio.governance import save_policy,reserve,budget_status
    from bcc.studio.runtime import StudioError
    model='openrouter:minimax/hailuo-3-max'
    await save_policy(env.svc,{'enabled':True,'free_only':False,'cloud_budget_usd':0.10,'per_job_usd':0.10,'prices':{model:0.06}})
    cls=openrouter.OpenRouterProvider;entered=asyncio.Event()
    async def serve(r):
        if r.url.path.endswith('/auth/key'):return httpx.Response(200,json={'data':{'limit_remaining':100}})
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(openrouter,'OpenRouterProvider',lambda key,**kw:cls(key,transport=httpx.MockTransport(serve),**kw))
    monkeypatch.setenv('OPENROUTER_API_KEY','test-only-key')
    job=await create(env,model=model,settings={})
    worker=asyncio.create_task(process_one(env.svc))
    await asyncio.wait_for(entered.wait(),5)
    assert (await budget_status(env.svc))['committed_upper_bound_usd']==0.06
    await env.client.post('/api/studio/jobs/'+str(job['id'])+'/cancel')
    await asyncio.wait_for(worker,5)
    assert (await budget_status(env.svc))['committed_upper_bound_usd']==0.06,'отмена вернула деньги'
    with pytest.raises(StudioError,match='budget') as refused:await reserve(env.svc,model,job['id']+1,1,[])
    assert refused.value.verdict=='OWNER_REQUIRED'
    assert (await budget_status(env.svc))['committed_upper_bound_usd']==0.06


async def test_provider_charge_above_the_declared_bound_keeps_bytes_and_stops_the_cloud(env,monkeypatch):
    """Провайдер списал больше объявленного потолка — облако выключается.

    Байты уже получены и проверены, поэтому они остаются у владельца: удалять
    оплаченный результат было бы вторым ущербом. Но следующий вызов не
    делается — политика переводится в enabled=false, и очередная задача
    получает unauthorized. Тихо принять перерасход значило бы позволить
    провайдеру назначать цену задним числом.
    """
    from bcc.studio.providers import openrouter
    from bcc.studio.governance import save_policy,policy
    cls=openrouter.OpenRouterProvider
    def serve(r):
        if r.url.path.endswith('/auth/key'):return httpx.Response(200,json={'data':{'limit_remaining':100}})
        return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(_png(256,256)).decode()}],'usage':{'cost':5.0}})
    monkeypatch.setattr(openrouter,'OpenRouterProvider',lambda key,**kw:cls(key,transport=httpx.MockTransport(serve),**kw))
    monkeypatch.setenv('OPENROUTER_API_KEY','test-only-key')
    model='openrouter:google/gemini-3.1-flash-image'
    await save_policy(env.svc,{'enabled':True,'free_only':False,'cloud_budget_usd':1,'per_job_usd':1,'prices':{model:0.01}})
    job=await create(env,model=model,settings={'aspect_ratio':'16:9'})
    await process_one(env.svc)
    row=(await env.client.get('/api/studio/jobs/'+str(job['id']))).json()
    assert row['status']=='failed' and row['studio']['reason']=='budget'
    assert row['studio']['verdict']=='OWNER_REQUIRED'
    runs=(await env.client.get('/api/studio/runs')).json()['items']
    assert len(runs)==1 and runs[0]['provenance']['cost_usd']==5.0,'оплаченные байты не выбрасываются'
    assert (await policy(env.svc))['enabled'] is False
    again=await create(env,model=model,settings={'aspect_ratio':'16:9'})
    await process_one(env.svc)
    after=(await env.client.get('/api/studio/jobs/'+str(again['id']))).json()
    assert after['status']=='failed' and after['studio']['reason']=='unauthorized'


async def test_reference_egress_survives_the_windows_positional_read_fallback(env,monkeypatch):
    """Референс уходит провайдеру целиком и на Windows — второй участок BL-081.

    `inputs_for` читал проверенный дескриптор последовательно. На Windows
    проверка оставляет общий указатель в конце файла, поэтому прочиталось бы
    ноль байт, и владелец получил бы «Reference changed during read» на
    референс, который не менялся. Путь облачный и в CI закрыт OWNER_REQUIRED,
    то есть на Windows он не исполнялся ни разу и ни один тест этого не ловил.
    Ветка Windows подставляется байт-в-байт.
    """
    import os,threading
    from bcc.video_studio import media
    from bcc.studio.dispatch import inputs_for
    from bcc.studio.integrations import import_reference
    seek=threading.Lock()
    def windows_pread(fd,length,offset):
        with seek:
            os.lseek(fd,offset,os.SEEK_SET)
            return os.read(fd,length)
    monkeypatch.setattr(media,'pread',windows_pread)
    png=_png(256,256)
    row=await import_reference(env.svc,'ref.png',base64.b64encode(png).decode())
    prepared=await inputs_for(env.svc,[{'run_id':row['id'],'role':'reference','sha256':row['sha256']}])
    assert len(prepared)==1
    head,_,payload=prepared[0]['data_uri'].partition(',')
    assert head=='data:image/png;base64'
    assert base64.b64decode(payload)==png,'референс передан не целиком'
