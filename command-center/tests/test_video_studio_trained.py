from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from bcc.video_studio.commands import apply_command
from bcc.video_studio.model import new_project,new_clip
from bcc.video_studio.trained import validate_draft,provider_configuration,propose


def project():
    p=new_project('trained_project','Test')
    p['media']['media']={'id':'media','name':'TEST','relative_path':'media/test.mp4','sha256':'a'*64,'bytes':100,
        'duration_ticks':10_000_000,'width':320,'height':180,'fps':{'num':25,'den':1},'has_video':True,'has_audio':True}
    p['sequences'][0]['tracks'][0]['clips']=[new_clip({'id':'unseen_take_0','media_id':'media','source_in':0,'source_out':10_000_000})]
    return p


def test_valid_reverse_is_pure_draft():
    p=project();before=deepcopy(p)
    result=validate_draft('{"type":"clip.reverse","clip_id":"unseen_take_0","reverse":true}',p,'unseen_take_0')
    assert result['valid'] and result['applicable'] and result['mode']=='draft_only'
    assert p==before
    changed,_,_=apply_command(p,result['command'])
    assert changed['sequences'][0]['tracks'][0]['clips'][0]['reverse'] is True


@pytest.mark.parametrize('raw',[
    '{"type":"clip.reverse","clip_id":" unseen_take_0","reverse":true}',
    '{"type":"clip.reverse","clip_id":"another_clip","reverse":true}',
    '{"type":"clip.trim","clip_id":"unseen_take_0","source_in":9000000,"source_out":1000000}',
    '{"type":"clip.audio","clip_id":"unseen_take_0","patch":{"volume":-1}}',
    '{"type":"clip.transform","clip_id":"unseen_take_0","patch":{"opacity":0.5,"x":1}}',
    '{"type":"project.archive","clip_id":"unseen_take_0"}',
    '{"type":"clip.reverse","clip_id":"unseen_take_0","reverse":true,"path":"secret"}',
    '{"type":"clip.reverse","clip_id":"bad","clip_id":"unseen_take_0","reverse":true}',
    '{"type":"clip.audio","clip_id":"unseen_take_0","patch":{"volume":NaN}}',
    '```json\n{"type":"clip.reverse","clip_id":"unseen_take_0","reverse":true}\n```',
    '[]',
])
def test_invalid_prediction_not_repaired_or_applied(raw):
    p=project();before=deepcopy(p)
    result=validate_draft(raw,p,'unseen_take_0')
    assert not result['valid'] and not result['applicable'] and result['validation_error']
    assert result['raw']==raw and p==before


def test_locked_track_cannot_get_applicable_draft():
    p=project();p['sequences'][0]['tracks'][0]['locked']=True
    assert not validate_draft('{"type":"clip.reverse","clip_id":"unseen_take_0","reverse":true}',p,'unseen_take_0')['valid']


@pytest.mark.parametrize('url',['https://example.com/v1','http://192.168.1.4:8879/v1','http://localhost:8879/v1','http://127.0.0.1:8879/v1?redirect=evil','http://user:pass@127.0.0.1:8879/v1'])
def test_no_remote_provider_configuration(url,monkeypatch):
    monkeypatch.setenv('BOSSMAN_VIDEO_TRAINED_URL',url)
    with pytest.raises(ValueError):provider_configuration()


def server_module():
    path=Path(__file__).resolve().parents[2]/'tools/video_studio/serve_adapter.py'
    spec=importlib.util.spec_from_file_location('video_adapter_server_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('change',[
    {'max_tokens':129},{'max_tokens':True},{'tools':[{}]},{'stream':True},{'model':'other'},
    {'messages':[{'role':'user','content':'a'*3501}]},{'messages':[{'role':'tool','content':'x'}]},
])
def test_server_request_bounds(change):
    data={'model':'bossman-video-lora','messages':[{'role':'user','content':'reverse'}],**change}
    with pytest.raises(ValueError):server_module().validate_request(data)


@pytest.mark.asyncio
async def test_server_auth_origin_and_no_model_execution():
    module=server_module()
    class FakeRuntime:
        identity='digest';model=None;report={}
        def generate(self,*args):raise AssertionError('must not run')
    app=module.create_app(FakeRuntime(),'x'*32)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
        assert (await client.get('/v1/models')).status_code==401
        assert (await client.get('/v1/models',headers={'Authorization':'Bearer '+'x'*32,'Origin':'https://evil.example'})).status_code==403
        assert (await client.get('/v1/models',headers={'Authorization':'Bearer '+'x'*32})).status_code==200
        assert (await client.post('/v1/chat/completions',headers={'Authorization':'Bearer '+'x'*32},json={'model':'bossman-video-lora','messages':[],'tools':[{}]})).status_code==400


@pytest.mark.asyncio
async def test_real_trained_model_proposal(monkeypatch):
    token=Path(__file__).resolve().parents[2]/'.audit-work/video-adapter-run01/server.token'
    if not token.is_file():pytest.skip('optional trained server token not configured')
    monkeypatch.setenv('BOSSMAN_VIDEO_TRAINED_TOKEN_FILE',str(token))
    monkeypatch.setenv('BOSSMAN_VIDEO_TRAINED_URL','http://127.0.0.1:8879/v1')
    p=project();before=deepcopy(p)
    result=await propose('Нужен reverse для unseen_take_0, показывай его с конца.',p,'unseen_take_0')
    assert result['valid'],result
    assert result['command']=={'type':'clip.reverse','clip_id':'unseen_take_0','reverse':True}
    assert result['usage']['tokens_out']>0 and p==before
