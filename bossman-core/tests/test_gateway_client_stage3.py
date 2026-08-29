import json
import httpx
import pytest
from bossman.gateway.client import GatewayClient

@pytest.mark.asyncio
async def test_gateway_client_chat_and_embeddings():
    async def handler(req):
        body=json.loads(req.content)
        if req.url.path.endswith('/chat/completions'):
            assert req.headers['authorization']=='Bearer k'
            return httpx.Response(200,json={'model':body['model'],'choices':[{'message':{'role':'assistant','content':'ok'}}]})
        return httpx.Response(200,json={'data':[{'embedding':[1.0]}]})
    c=GatewayClient('http://gateway/v1','k')
    await c._client.aclose()
    c._client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        assert (await c.chat(model='bossman-smart',messages=[]))['model']=='bossman-smart'
        assert (await c.embeddings(model='bossman-embed',input='x'))['data'][0]['embedding']==[1.0]
    finally:
        await c.close()
