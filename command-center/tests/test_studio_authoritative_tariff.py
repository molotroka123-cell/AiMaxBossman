"""PREP-03: provider tariff truth must precede every live generation POST.

No live provider is contacted here. MockTransport records the order of HTTP
operations and turns any unexpected generation POST into a test failure.
"""
import httpx
import pytest

from bcc.studio.provider import GenerationPlane
from bcc.studio.providers.openrouter import OpenRouterProvider, _pricing_state
from bcc.studio.runtime import StudioError

MODEL='openrouter:google/gemini-3.1-flash-image'
REMOTE_MODEL='google/gemini-3.1-flash-image'


async def _free_only_gate():
    # This is deliberately the vulnerable historical input: the owner-local
    # estimate says zero. It must not be accepted as provider truth.
    return {'kind':'cloud','pricing_known':True,'price_in':0,'price_out':0,'free_only':True}


def test_authoritative_pricing_classifier_fails_closed():
    assert _pricing_state({'prompt':'0','completion':'0','image_output':'0'})=='free'
    assert _pricing_state({'prompt':'0','image_output':'0.00006'})=='paid'
    assert _pricing_state({})=='unknown'
    assert _pricing_state({'prompt':'not-a-price'})=='unknown'
    assert _pricing_state({'prompt':'-0.01'})=='unknown'


async def test_paid_provider_tariff_blocks_local_zero_before_generation_post():
    calls=[]
    def serve(request):
        calls.append((request.method,request.url.path))
        assert 'authorization' not in request.headers,'public catalog lookup leaked owner key'
        if request.method=='GET' and request.url.path=='/api/v1/models':
            return httpx.Response(200,json={'data':[{
                'id':REMOTE_MODEL,
                'pricing':{'prompt':'0','completion':'0','image_output':'0.00006'},
                'supported_parameters':['image_size'],
            }]})
        pytest.fail(f'generation request escaped authoritative tariff gate: {request.method} {request.url.path}')

    provider=OpenRouterProvider(
        'test-secret',transport=httpx.MockTransport(serve),gate=_free_only_gate,
        enforce_authoritative=True,
    )
    with pytest.raises(StudioError) as caught:
        await provider.submit(GenerationPlane(MODEL,'test',{'aspect_ratio':'16:9'}))
    assert caught.value.reason=='budget'
    assert caught.value.verdict=='OWNER_REQUIRED'
    assert calls==[('GET','/api/v1/models')]
    assert provider.authoritative_pricing[REMOTE_MODEL]['state']=='paid'


async def test_authoritative_zero_tariff_allows_generation_only_after_catalog_get():
    calls=[]
    def serve(request):
        calls.append((request.method,request.url.path))
        if request.url.path=='/api/v1/models':
            assert request.method=='GET'
            return httpx.Response(200,json={'data':[{
                'id':REMOTE_MODEL,
                'pricing':{'prompt':'0','completion':'0','image_output':'0'},
            }]})
        if request.url.path=='/api/v1/images':
            assert request.method=='POST'
            assert request.headers['Authorization']=='Bearer test-secret'
            return httpx.Response(200,json={'data':[{'b64_json':'AA=='}],'usage':{'cost':0}})
        pytest.fail(f'unexpected request {request.method} {request.url.path}')

    provider=OpenRouterProvider(
        'test-secret',transport=httpx.MockTransport(serve),gate=_free_only_gate,
        enforce_authoritative=True,
    )
    receipt=await provider.submit(GenerationPlane(MODEL,'test'))
    assert receipt.request_id
    assert calls==[('GET','/api/v1/models'),('POST','/api/v1/images')]
    assert provider.authoritative_pricing[REMOTE_MODEL]['state']=='free'


async def test_missing_exact_model_blocks_before_generation_post():
    calls=[]
    def serve(request):
        calls.append((request.method,request.url.path))
        if request.url.path=='/api/v1/models':
            return httpx.Response(200,json={'data':[{'id':'different/model','pricing':{'prompt':'0'}}]})
        pytest.fail('generation POST must not occur for an absent exact model')

    provider=OpenRouterProvider(
        'test-secret',transport=httpx.MockTransport(serve),gate=_free_only_gate,
        enforce_authoritative=True,
    )
    with pytest.raises(StudioError) as caught:
        await provider.submit(GenerationPlane(MODEL,'test'))
    assert caught.value.reason=='unknown_price'
    assert caught.value.verdict=='OWNER_REQUIRED'
    assert calls==[('GET','/api/v1/models')]
