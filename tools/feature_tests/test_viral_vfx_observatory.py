"""Feature-branch acceptance: real FFmpeg, canonical HTTP/SQLite; no model calls.

Run explicitly in an environment with Command Center dependencies and FFmpeg.
These tests are not owner Windows/GPU/live-generation certification.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from bcc import db
from bcc.api import create_app
from bcc.config import Settings
from bcc.video_studio import viral_vfx as vfx
from bcc.video_studio.commands import apply_command
from bcc.video_studio.model import new_project, new_clip, validate_project, StudioError
from bcc.video_studio.render import render_project
from bcc.video_studio.media import MediaLibrary


def ffmpeg(*args, timeout=30):
    result = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', *args], capture_output=True, timeout=timeout)
    assert result.returncode == 0, result.stderr.decode(errors='replace')[-2000:]
    return result.stdout


def pixels(graph):
    return ffmpeg('-f','lavfi','-i','testsrc2=s=128x72:r=30:d=0.4','-filter_threads','1',
                  '-vf','format=rgba,'+graph+',format=rgb24','-frames:v','12','-threads','1','-f','rawvideo','pipe:1')


@pytest.mark.parametrize('preset',[p['id'] for p in vfx.catalog()['presets']])
def test_every_preset_has_real_pixel_effect_and_preserves_constant_alpha(preset):
    graph=vfx.compile_filter({'preset':preset,'intensity':.7,'bpm':140,'offset':.1})
    before=pixels('null');after=pixels(graph)
    assert len(after)==len(before)==128*72*3*12
    assert hashlib.sha256(after).digest()!=hashlib.sha256(before).digest()
    raw=ffmpeg('-f','lavfi','-i','color=blue@0.25:s=32x32:r=30:d=0.1,format=rgba,'+graph,
               '-filter_threads','1','-vf','format=rgba','-frames:v','1','-threads','1','-pix_fmt','rgba','-f','rawvideo','pipe:1')
    assert len(raw)==32*32*4 and set(raw[3::4])=={63}
    assert vfx.compile_filter({'preset':preset,'intensity':0})=='null'


@pytest.mark.parametrize('patch',[{'preset':'evil;movie=/etc/passwd'},{'preset':None},
 {'bpm':True},{'bpm':0},{'bpm':201},{'offset':float('nan')},{'offset':61},
 {'intensity':float('inf')},{'intensity':-1},{'intensity':'0.5'},{'url':'https://example.com'},
 {'expression':'1;movie=file'},{'intensity':None}])
def test_invalid_params_refused(patch):
    with pytest.raises(ValueError):vfx.compile_filter({'preset':'phonk_hard',**patch})


def test_shared_beat_phase_catalog_is_copy_and_not_generation():
    p={'preset':'punch_zoom','bpm':120,'offset':.1}
    assert vfx.compile_filter(p,start_seconds=0)==vfx.compile_filter(p,start_seconds=5)
    cat=vfx.catalog();cat['presets'][0]['name']='MUTATED'
    assert vfx.catalog()['presets'][0]['name']!='MUTATED'
    assert cat['automatic_beat_detection'] is False and cat['generation'] is False
    assert len(cat['presets'])==30 and len(cat['shots'])==3


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('BOSSMAN_OFFLINE_MODE_ENABLED','1')
    app=create_app(Settings(data_dir=tmp_path/'app'),start_workers=False,announce_token=False)
    with TestClient(app) as client:
        client.headers['X-BCC-Token']=app.state.svc.auth.token
        yield client,app.state.svc


async def seed(svc):
    now=db.utcnow();private='PRIVATE_FIXTURE_NOT_FOR_OBSERVATORY'
    async with svc.db.session() as s:
        await s.execute(sa.insert(db.providers).values(id=101,name=private,kind='openai_compat',base_url='http://127.0.0.1:9'))
        await s.execute(sa.insert(db.models).values(id=101,provider_id=101,name=private,alias=private,kind='local'))
        await s.execute(sa.insert(db.agents).values(id=101,name=private,model_id=101))
        await s.execute(sa.insert(db.tasks),[{'id':101,'prompt':private,'title':private,'agent_id':101,'status':'draft'}, {'id':102,'prompt':private,'title':private,'agent_id':101,'status':'draft'}])
        await s.execute(sa.insert(db.task_runs),[{'id':101,'task_id':101,'status':'completed','model_alias':private,'result':private,'tokens_in':10,'tokens_out':5,'cost_usd':0.0}, {'id':102,'task_id':102,'status':'completed','model_alias':private,'result':private,'tokens_in':20,'tokens_out':2,'cost_usd':0.0}])
        await s.execute(sa.insert(db.tool_calls).values(id=101,run_id=101,task_id=101,call_id='fixture-call',tool='UNTRUSTED_'+private,args={'token':private},result_preview=private,status='executed'))
        await s.execute(sa.insert(db.events).values(kind='verification.result',data={'run_id':102,'status':'VERIFIED','secret':private}))
        for i,project,run in [(101,'A',101),(102,'A',101),(103,'B',102)]:
            await s.execute(sa.insert(db.facts).values(id=i,subject=private,predicate=private,object=private,statement=private,valid_at=now,source_kind='run',source_run_id=run,meta={'project_id':project,'secret':private}))
        await s.execute(sa.update(db.facts).where(db.facts.c.id==101).values(superseded_by=103))
        await s.execute(sa.insert(db.skills).values(id=101,name='fixture-skill',slug='fixture-skill'))
        for i in [101,102]:await s.execute(sa.insert(db.skill_versions).values(id=i,skill_id=101,version=i,process=private))
        await s.execute(sa.insert(db.skill_evaluations).values(id=101,skill_id=101,baseline_version_id=101,candidate_version_id=102,status='decided',verdict='promote',applied=False,reason=private,metrics={'baseline':{'runs':True,'success_rate':1.7,'avg_duration_ms':-2},'candidate':{'runs':8,'success_rate':.75,'avg_duration_ms':5},'secret':private}))
        await s.commit()
    return private


def test_observatory_real_db_scope_privacy_and_learning_truth(client):
    c,svc=client;private=c.portal.call(seed,svc)
    r=c.get('/api/observatory/snapshot?task_id=101&project_id=A');assert r.status_code==200,r.text
    assert private not in r.text
    d=r.json();assert d['read_only'] is True
    assert [r['id'] for r in d['brain']['runs']]==[101]
    assert d['brain']['tool_calls'][0]['tool']=='UNREGISTERED_TOOL'
    assert d['brain']['recorded_verifications']==[]
    assert {f['id'] for f in d['memory']['facts']}=={101,102}
    old=next(f for f in d['memory']['facts'] if f['id']==101)
    assert old['superseded_by'] is None and old['superseded_outside_window']
    e=d['learning']['evaluations'][0]
    assert e['verdict']=='promote' and e['applied'] is False and d['learning']['view_can_promote'] is False
    assert e['baseline_runs'] is None and e['baseline_success_rate'] is None and e['baseline_avg_duration_ms'] is None
    assert (e['candidate_runs'],e['candidate_success_rate'],e['candidate_avg_duration_ms'])==(8,.75,5)
    assert c.get('/api/observatory/snapshot').json()['memory']['facts']==[]
    assert c.get('/api/observatory/snapshot?task_id=9999').status_code==404
    assert c.get('/api/observatory/snapshot?project_id=../A').status_code==422
    assert c.get('/api/observatory/snapshot?task_id=-1').status_code==422
    # The new panels do not modify original records or apply evaluations.
    async def state():
        async with svc.db.session() as s:return (await s.execute(sa.select(db.skill_evaluations.c.applied).where(db.skill_evaluations.c.id==101))).scalar()
    assert c.portal.call(state) is False


def test_routes_auth_no_write_and_failure_is_actionable(client,monkeypatch):
    c,svc=client
    for path in ['/api/observatory/snapshot','/api/observatory/routes','/api/video-studio/vfx-catalog']:
        assert c.get(path,headers={'X-BCC-Token':'invalid'}).status_code==401
        assert c.post(path,json={}).status_code==405
    routes=c.get('/api/observatory/routes').json()['routes']
    assert any(r['path']=='/api/video-studio/commands' and 'POST' in r['methods'] for r in routes)
    assert all(r['evidence']=='DECLARED_ROUTE_NOT_EXECUTION' for r in routes)
    from bcc.features import observatory
    async def fail(*args):raise sa.exc.OperationalError('private SQL',{},Exception('PRIVATE_DB_SECRET'))
    monkeypatch.setattr(observatory,'snapshot',fail)
    r=c.get('/api/observatory/snapshot');assert r.status_code==503 and 'PRIVATE_DB_SECRET' not in r.text


def test_observatory_caps_window(client):
    c,svc=client
    async def fill():
        async with svc.db.session() as s:
            await s.execute(sa.insert(db.tasks),[{'prompt':'fixture','status':'draft'} for _ in range(55)]);await s.commit()
    c.portal.call(fill)
    data=c.get('/api/observatory/snapshot').json()
    assert len(data['tasks'])==50 and data['truncated']['tasks'] is True
    assert data['analysis']['counts']['draft']==50


def test_http_vfx_dry_apply_replay_conflict_reopen_undo(client):
    c,svc=client
    project=c.post('/api/video-studio/projects',json={'name':'VFX fixture','operation_id':'create-vfx'}).json()['project']
    pid=project['id'];track=project['sequences'][0]['tracks'][0]['id']
    def command(cmd,revision,operation,dry=False):
        return c.post('/api/video-studio/commands',json={'project_id':pid,'expected_revision':revision,'operation_id':operation,'command':cmd,'dry_run':dry})
    added=command({'type':'title.add','track_id':track,'duration':15000000,'title':{'text':'SAFE FIXTURE'}},0,'add-title');assert added.status_code==200,added.text
    clip_id=added.json()['changed_ids'][0]
    op={'type':'effect.apply','clip_id':clip_id,'effect':{'id':'viral-vfx','type':'viral_vfx','params':{'preset':'phonk_hard','intensity':.5,'bpm':140,'offset':0}}}
    dry=command(op,1,'vfx-once',True);assert dry.status_code==200 and dry.json()['dry_run'] is True
    assert c.get('/api/video-studio/projects/'+pid).json()['revision']==1
    a=command(op,1,'vfx-once');assert a.status_code==200,a.text
    b=command(op,1,'vfx-once');assert b.status_code==200 and b.json()['revision']==2
    assert command(op,1,'stale').status_code==409
    current=c.get('/api/video-studio/projects/'+pid).json()
    assert len(current['sequences'][0]['tracks'][0]['clips'][0]['effects'])==1
    from bcc.video_studio.store import ProjectStore
    reopened=c.portal.call(ProjectStore(svc.db).get,pid)
    assert reopened==current
    undone=command({'type':'history.undo'},2,'undo-vfx');assert undone.status_code==200
    assert undone.json()['project']['sequences'][0]['tracks'][0]['clips'][0]['effects']==[]
    # Audio track targets must fail, not silently accept a visual no-op.
    op.pop('clip_id');op['track_id']=current['sequences'][0]['tracks'][1]['id']
    assert command(op,3,'reject-audio').status_code==422


def test_native_fifteen_second_export_full_decode_audio_and_frame_count(tmp_path):
    source=tmp_path/'source.mp4'
    ffmpeg('-f','lavfi','-i','testsrc2=s=180x320:r=30:d=15','-f','lavfi','-i','sine=frequency=220:sample_rate=48000:duration=15','-c:v','libx264','-preset','ultrafast','-crf','25','-threads','1','-c:a','aac','-shortest',str(source))
    async def render():
        library=MediaLibrary(tmp_path);media=await library.import_file(source,name='synthetic source')
        project=new_project('vfx-render','VFX render fixture');seq=project['sequences'][0];seq.update(width=180,height=320,fps={'num':30,'den':1})
        project['media'][media['id']]=media
        seq['tracks'][0]['clips'].append(new_clip({'media_id':media['id'],'source_out':15000000,'effects':[{'id':'vfx','enabled':True,'type':'viral_vfx','params':{'preset':'phonk_hard','intensity':.5,'bpm':140,'offset':0}}]}))
        validate_project(project)
        before=copy.deepcopy(project)
        result=await render_project(project,tmp_path,tmp_path/'result.mp4',{'preset':'ultrafast','crf':25})
        assert project==before
        return result
    result=asyncio.run(render());v=result['verification']
    assert v['passed'] and v['decoded'] and v['decoded_video_frames']==450
    assert v['has_audio'] and v['has_video'] and (v['width'],v['height'])==(180,320)
    assert abs(v['duration_ticks']-15000000)<150000 and len(v['sample_frames'])>=2
    if os.environ.get('VFX_EVIDENCE_DIR'):
        out=Path(os.environ['VFX_EVIDENCE_DIR']);out.mkdir(parents=True,exist_ok=True)
        (out/'native-render.json').write_text(json.dumps(v,indent=2),encoding='utf-8')


def test_real_chromium_six_views_vfx_apply_and_studio_recipe(tmp_path,monkeypatch):
    """Actual browser, API, SQLite and canonical editor; no external provider."""
    import socket
    import threading
    import time
    import uvicorn
    from playwright.sync_api import sync_playwright, expect
    monkeypatch.setenv('BOSSMAN_OFFLINE_MODE_ENABLED','1')
    settings=Settings(data_dir=tmp_path/'browser-app')
    setup_app=create_app(settings,start_workers=False,announce_token=False)
    with TestClient(setup_app) as c:
        c.headers['X-BCC-Token']=setup_app.state.svc.auth.token
        c.portal.call(seed,setup_app.state.svc)
        p=c.post('/api/video-studio/projects',json={'name':'Browser VFX fixture','operation_id':'browser-create'}).json()['project']
        pid=p['id'];track=p['sequences'][0]['tracks'][0]['id']
        r=c.post('/api/video-studio/commands',json={'project_id':pid,'expected_revision':0,'operation_id':'browser-title','command':{'type':'title.add','track_id':track,'duration':15000000,'title':{'text':'SAFE FIXTURE'}}})
        assert r.status_code==200,r.text
        clip_id=r.json()['changed_ids'][0];token=setup_app.state.svc.auth.token
    # Separate app lifecycle and connection pool over the same persisted data.
    app=create_app(settings,start_workers=False,announce_token=False)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on',access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    try:
        deadline=time.monotonic()+20
        while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.05)
        assert server.started,'test server failed to start'
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,**({'executable_path':os.environ['BCC_TEST_CHROMIUM']} if os.environ.get('BCC_TEST_CHROMIUM') else {}))
            try:
                page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];bad=[];failed=[];posts=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('console',lambda m:errors.append(m.text) if m.type=='error' else None)
                page.on('response',lambda r:bad.append((r.status,r.url)) if r.status>=400 and '/api/' in r.url else None)
                page.on('requestfailed',lambda r:failed.append((r.url,r.failure)) if r.failure!='net::ERR_ABORTED' else None)
                page.on('request',lambda r:posts.append(r.url) if r.method=='POST' else None)
                page.goto(f'http://127.0.0.1:{port}/#/observatory?task=101')
                page.locator('#login-token').fill(token);page.locator('#login-submit').click()
                expect(page.locator('.obs-root h1')).to_be_visible(timeout=20000)
                bad.clear();errors.clear();failed.clear();posts.clear()
                for name,title in [('Brain View','Инструменты и права'),('Live Agent Graph','Агенты → задачи → запуски → инструменты'),('Memory Map','Память: происхождение и замены'),('Learning View','Сравнение версий навыков'),('Self-Analysis','Самоанализ по наблюдаемым данным')]:
                    page.get_by_role('button',name=name,exact=True).click()
                    expect(page.get_by_role('heading',name=title,exact=True)).to_be_visible()
                page.get_by_role('button',name='UI → API X-Ray',exact=True).click()
                page.get_by_role('button',name='Включить X-Ray',exact=True).click()
                expect(page.locator('.obs-panel')).to_contain_text('ON ·')
                page.evaluate('(p)=>location.hash=p',f'/video-studio?project_id={pid}')
                expect(page.locator(f'[data-clip-id="{clip_id}"]')).to_be_visible(timeout=20000)
                page.locator(f'[data-clip-id="{clip_id}"]').click()
                page.get_by_role('button',name='Viral VFX · Phonk',exact=True).click()
                dialog=page.locator('dialog.viral-vfx-dialog');expect(dialog).to_be_visible()
                dialog.get_by_role('button',name='Проверить VFX-план').click()
                expect(dialog).to_contain_text('План проверен без записи')
                response=page.request.get(f'http://127.0.0.1:{port}/api/video-studio/projects/{pid}')
                assert response.json()['revision']==1
                dialog.get_by_role('button',name='Применить VFX').dblclick()
                expect(dialog).to_contain_text('Сохранено r2')
                assert page.request.get(f'http://127.0.0.1:{port}/api/video-studio/projects/{pid}').json()['revision']==2
                dialog.get_by_role('button',name='Закрыть',exact=True).click()
                page.evaluate("location.hash='/observatory?view=xray&task=101'")
                expect(page.locator('.obs-panel')).to_contain_text('VideoStudio.command.apply')
                expect(page.locator('.obs-panel')).to_contain_text('VideoStudio.command.dry_run')
                page.evaluate("location.hash='/images?studio=1'")
                page.locator('.viral-shot-recipes summary').click()
                page.get_by_role('button',name='Показать 3 сцены').click()
                page.get_by_role('button',name='Вставить только промпт').nth(1).click()
                expect(page.locator('.studio-prompt')).to_have_value(__import__('re').compile('.*Controlled orbit.*',__import__('re').S))
                assert not any('/api/studio/jobs' in path for path in posts),'recipe submitted a provider job'
                page.reload();expect(page.locator('.studio-surface')).to_be_visible()
                page.evaluate('(p)=>location.hash=p',f'/video-studio?project_id={pid}')
                expect(page.locator(f'[data-clip-id="{clip_id}"]')).to_be_visible()
                stored=page.request.get(f'http://127.0.0.1:{port}/api/video-studio/projects/{pid}').json()
                assert stored['sequences'][0]['tracks'][0]['clips'][0]['effects'][0]['type']=='viral_vfx'
                page.evaluate("location.hash='/observatory?view=agents&task=101'")
                expect(page.locator('.obs-graph svg')).to_be_visible()
                assert 'PRIVATE_FIXTURE_NOT_FOR_OBSERVATORY' not in page.locator('.obs-root').inner_text()
                if os.environ.get('VFX_EVIDENCE_DIR'):
                    out=Path(os.environ['VFX_EVIDENCE_DIR']);out.mkdir(parents=True,exist_ok=True)
                    page.screenshot(path=str(out/'observatory-browser.png'),full_page=True)
                    (out/'browser.json').write_text(json.dumps({'pageerrors_console':errors,'unexpected_http':bad,'requestfailed':failed,'views':6,'provider_jobs_submitted':0},indent=2))
                assert not errors,errors
                assert not bad,bad
                assert not failed,failed
            finally:browser.close()
    finally:
        server.should_exit=True;thread.join(timeout=15);sock.close()
        assert not thread.is_alive(),'owned test server did not stop'


def test_chromium_dom_components_six_views_and_vfx_review(tmp_path):
    """Real Chromium DOM, fixture API. No navigation, server or model evidence."""
    import re
    from playwright.sync_api import sync_playwright, expect
    import bcc
    package = Path(bcc.__file__).resolve().parent
    ui = package / '_ui' if (package / '_ui').is_dir() else package.parent / 'ui'
    def module(path):
        source=(ui/path).read_text(encoding='utf-8')
        source=re.sub(r'^import .*?;\n','',source,flags=re.M)
        return source.replace('export function ','function ').replace('export async function ','async function ').replace('export const ','const ').replace('export class ','class ')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,**({'executable_path':os.environ['BCC_TEST_CHROMIUM']} if os.environ.get('BCC_TEST_CHROMIUM') else {}))
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.set_content('<html><head><style id="observatory-style"></style></head><body><main id="mount"></main></body></html>')
            page.add_style_tag(content=(ui/'pages/observatory.css').read_text())
            # about:blank has no secure-origin randomUUID. Fixture identity only;
            # production entropy is not covered by this DOM-only test.
            page.evaluate("()=>{let n=0;Object.defineProperty(crypto,'randomUUID',{value:()=> 'dom-fixture-operation-'+(++n),configurable:true});}")
            page.add_script_tag(content='window.widgets=(()=>{'+module('components.js')+';return {h,toastError,toastOk};})();')
            page.add_script_tag(content='window.trace=(()=>{'+module('api_trace.js')+';return {setTraceEnabled,setTraceRoutes,traceSnapshot,clearTrace};})();')
            page.add_script_tag(content='''window.fixture={schema_version:1,source:'SYNTHETIC_DOM_FIXTURE',observed_at:'NOT_LIVE',tasks:[{id:1,agent_id:2,status:'running'}],truncated:{},brain:{runs:[{id:3,task_id:1,status:'running',model_id:4,model_kind:'local',tokens_in:3,tokens_out:2,cost_usd:0}],tool_calls:[{id:5,run_id:3,step:1,tool:'fixture_tool',effect:'ask',status:'pending_approval',duration_ms:null}],approvals:[],recorded_verifications:[]},memory:{scope:'TASK_RUN_LINEAGE',facts:[{id:6,source_run_id:3,source_kind:'run',superseded_by:null,confidence:null}],truncated:false},learning:{evaluations:[],truncated:false},analysis:{observations:[{code:'WAITING_APPROVAL',count:1}]}};
window.calls=[];window.nav=[];window.ctx={navigate:(id,p)=>nav.push({id,p}),refresh:()=>{},bus:{state:'FIXTURE'}};
window.api={raw:async(path,opts)=>{calls.push({path,opts});return path.includes('/routes')?{routes:[]}:structuredClone(fixture)}};''')
            source=module('pages/observatory.js').replace('export default {','const Observatory={')
            page.add_script_tag(content='(()=>{const {h,toastError}=widgets;const {setTraceEnabled,setTraceRoutes,traceSnapshot,clearTrace}=trace;'+source+';window.Observatory=Observatory;})();')
            for view,heading in [('brain','Инструменты и права'),('agents','Агенты → задачи → запуски → инструменты'),('memory','Память: происхождение и замены'),('learning','Сравнение версий навыков'),('xray','UI → API X-Ray'),('analysis','Самоанализ по наблюдаемым данным')]:
                page.evaluate("async v=>document.querySelector('#mount').replaceChildren(await Observatory.render(ctx,{view:v,task:'1'}))",view)
                expect(page.get_by_role('heading',name=heading,exact=True)).to_be_visible()
                assert page.locator('.obs-tabs button').count()==6
            page.evaluate("async()=>document.querySelector('#mount').replaceChildren(await Observatory.render(ctx,{view:'agents',task:'1'}))")
            page.locator('[data-node-id="t1"]').click()
            assert page.evaluate('nav.at(-1).p.task')=='1'
            # Explicit fixture model/API: this test asserts user wiring only.
            page.evaluate("""()=>{location.hash='/video-studio?project_id=p';document.querySelector('#mount').innerHTML='<div id="editor"><div class="vs-clip selected" data-clip-id="c"></div></div>';window.project={id:'p',revision:1,active_sequence_id:'s',media:{},sequences:[{id:'s',tracks:[{kind:'video',locked:false,clips:[{id:'c',title:{text:'fixture'}}]}]}]};}""")
            page.evaluate("""()=>{calls=[];api.raw=async(path,opts)=>{calls.push({path,opts});if(path.endsWith('vfx-catalog'))return {presets:[{id:'phonk_hard',name:'PHONK HARD'}],shots:[]};if(path.includes('/projects/'))return structuredClone(project);return opts.body.dry_run?{dry_run:true,revision:1}:{revision:2};};}""")
            source=module('pages/viral_vfx_panel.js')
            page.add_script_tag(content='(()=>{const {h,toastError,toastOk}=widgets;'+source+';window.openVfxPanel=openVfxPanel;})();')
            page.evaluate("()=>openVfxPanel(document.querySelector('#editor'),ctx)")
            dialog=page.locator('dialog.viral-vfx-dialog')
            expect(dialog.get_by_role('button',name='Применить VFX')).to_be_disabled()
            dialog.get_by_role('button',name='Проверить VFX-план').click()
            expect(dialog).to_contain_text('План проверен без записи')
            dialog.get_by_label('VFX BPM').fill('150')
            expect(dialog.get_by_role('button',name='Применить VFX')).to_be_disabled()
            dialog.get_by_role('button',name='Проверить VFX-план').click()
            dialog.get_by_role('button',name='Применить VFX').dblclick()
            expect(dialog).to_contain_text('Сохранено r2')
            posted=page.evaluate('calls.filter(c=>c.opts?.method==="POST")')
            assert len(posted)==3 and sum(c['opts']['body']['dry_run'] is False for c in posted)==1
            assert posted[-1]['opts']['body']['operation_id']==posted[-2]['opts']['body']['operation_id']
            assert posted[-1]['opts']['body']['command']['operations'][0]['effect']['params']['bpm']==150
            assert not errors,errors
        finally:browser.close()
