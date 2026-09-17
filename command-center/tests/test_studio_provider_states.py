import pytest
from bcc.model_health import ALL_STATUSES
from bcc.studio.provider import classify_response, GenerationProvider

@pytest.mark.parametrize('code,body,reason', [
    (401,{},'unauthorized'), (429,{},'throttled'),(402,{},'insufficient_credit'),
    (404,{},'provider_down'),(200,{'status':'nsfw'},'content_policy'),(200,'broken json','malformed'),
])
def test_success_and_distinct_refusal_pair(code,body,reason):
    good = classify_response(200, {'status':'completed','outputs':[{'ref':'asset-1'}]})
    assert good.state == 'completed' and good.reason is None
    bad = classify_response(code,body)
    assert bad.state != 'completed' and bad.reason == reason
    assert bad.health in ALL_STATUSES
    assert not good.is_evidence and not bad.is_evidence

@pytest.mark.parametrize('body,reason', [({},'malformed'),('', 'silent'),({'status':'completed','outputs':[]},'malformed'),({'status':'wat'},'malformed'),({'error':{'code':'insufficient_credit'}},'insufficient_credit'),({'status':'completed','outputs':['https://secret']},'malformed')])
def test_bad_success_never_completes(body,reason):
    status = classify_response(200,body)
    assert status.state != 'completed' and status.reason == reason

@pytest.mark.parametrize('state',['queued','running','canceled','timeout'])
def test_noncompletion_state_preserved(state):
    assert classify_response(200,{'status':state}).state == state


def test_credit_requires_owner_and_is_distinct_from_rate_limit():
    credit = classify_response(402,{})
    rate = classify_response(429,{})
    assert credit.reason != rate.reason
    assert credit.verdict == 'OWNER_REQUIRED'
    assert credit.health == rate.health == 'throttled'

@pytest.mark.asyncio
async def test_comfyui_lifecycle_and_local_cancellation(tmp_path):
    from bcc.oss.comfyui import ComfyUIImageProvider
    from bcc.studio.provider import GenerationPlane
    from bcc.studio.providers.comfyui import ComfyUIProvider
    class Client:
        calls = 0
        async def submit(self,workflow):
            self.calls += 1
            return 'prompt-1'
        async def status(self,request_id): return {'state':'pending','outputs':[]}
    client = Client()
    provider = ComfyUIProvider(ComfyUIImageProvider('http://127.0.0.1:8188','model.safetensors',client=client),tmp_path)
    assert isinstance(provider,GenerationProvider)
    plane = GenerationPlane('comfyui:*','test',{'width':512,'height':512})
    receipt = await provider.submit(plane)
    assert (await provider.status(receipt.request_id)).state == 'running'
    await provider.cancel(receipt.request_id)
    assert (await provider.status(receipt.request_id)).state == 'canceled'
    assert client.calls == 1
    assert receipt.cancel_ref is None  # no unsupported remote interruption promise
    with pytest.raises(ValueError,match='request_id'): await provider.status('foreign')
    with pytest.raises(ValueError,match='width'):
        await provider.submit(GenerationPlane('comfyui:*','test',{'width':257}))
    assert client.calls == 1

@pytest.mark.asyncio
async def test_comfyui_fetch_verifies_bytes_and_path(tmp_path):
    import hashlib
    import struct
    import zlib
    from bcc.oss.comfyui import ComfyUIImageProvider
    from bcc.studio.provider import GenerationPlane
    from bcc.studio.providers.comfyui import ComfyUIProvider
    def chunk(kind,data): return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    data = b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',256,256,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\0'*768)*256))+chunk(b'IEND',b'')
    class Client:
        async def submit(self,workflow): return 'p1'
        async def status(self,rid): return {'state':'completed','outputs':[{'filename':'x.png','type':'output'}]}
        async def download(self,descriptor): return self.data,'image/png',{}
    client = Client()
    client.data = data
    provider = ComfyUIProvider(ComfyUIImageProvider('http://127.0.0.1:8188','m.safetensors',client=client),tmp_path)
    receipt = await provider.submit(GenerationPlane('comfyui:*','test',{'width':256,'height':256}))
    output = (await provider.status(receipt.request_id)).outputs[0]
    fetched = await provider.fetch(output,tmp_path/'good.png')
    assert fetched.sha256 == hashlib.sha256(data).hexdigest()
    assert fetched.bytes == len(data) and fetched.width == 256
    with pytest.raises(PermissionError): await provider.fetch(output,tmp_path/'..'/'escape.png')
    client.data = b'corrupt'
    with pytest.raises(ValueError): await provider.fetch(output,tmp_path/'bad.png')
    assert not (tmp_path/'bad.png').exists()

@pytest.mark.parametrize('body',[{'status':[]},{'status':{}},{'status':'completed','outputs':[{'ref':None}]},{'status':'completed','outputs':[{}]}])
def test_malformed_shapes_return_reason_without_exception(body):
    result = classify_response(200,body)
    assert result.reason == 'malformed' and result.state == 'failed'

@pytest.mark.parametrize('code',[401,402,403,404,429,500])
def test_http_failure_wins_over_completed_payload(code):
    result = classify_response(code,{'status':'completed','outputs':[{'ref':'x'}]})
    assert result.state == 'failed' and not result.outputs


def test_status_constructor_cannot_combine_success_with_refusal():
    from bcc.studio.provider import ProviderStatus, ProviderOutput
    with pytest.raises(ValueError):
        ProviderStatus('completed','unauthorized',(ProviderOutput('x'),))
    with pytest.raises(ValueError): ProviderStatus('failed')

@pytest.mark.asyncio
@pytest.mark.parametrize('code,reason',[(401,'unauthorized'),(402,'insufficient_credit'),(429,'throttled'),(404,'provider_down')])
async def test_comfy_submit_error_has_safe_named_reason(tmp_path,code,reason):
    import httpx
    from bcc.oss.comfyui import ComfyUIImageProvider
    from bcc.studio.provider import GenerationPlane, ProviderFailure
    from bcc.studio.providers.comfyui import ComfyUIProvider
    class Client:
        async def submit(self,workflow):
            response = httpx.Response(code,request=httpx.Request('POST','http://127.0.0.1/prompt'))
            raise httpx.HTTPStatusError('secret-do-not-echo',request=response.request,response=response)
    provider = ComfyUIProvider(ComfyUIImageProvider('http://127.0.0.1:8188','m.safetensors',client=Client()),tmp_path)
    with pytest.raises(ProviderFailure) as caught:
        await provider.submit(GenerationPlane('comfyui:*','test'))
    assert caught.value.status.reason == reason
    assert 'secret-do-not-echo' not in str(caught.value)
