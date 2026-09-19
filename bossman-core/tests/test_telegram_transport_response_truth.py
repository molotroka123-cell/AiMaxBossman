"""Actual transport rejects false/invalid success; no live Telegram or secrets."""
import asyncio
import httpx
import pytest
from bossman.notifications import telegram_transport as module


@pytest.mark.parametrize('status,body', [(200,b'{"ok":false}'),(200,b'broken'),(200,b'[]'),
                                        (200,b'{"ok":true}'),(401,b'{"ok":false}')])
def test_unconfirmed_telegram_response_is_not_success(monkeypatch,status,body):
    client_type=httpx.AsyncClient
    def client(**kw):
        return client_type(transport=httpx.MockTransport(lambda r:httpx.Response(status,content=body)),**kw)
    monkeypatch.setattr(module.httpx,'AsyncClient',client)
    t=module.TelegramTransport(None,bot_token_provider=lambda:'fixture',chat_id_provider=lambda:'1',webhook_secret_provider=lambda:'')
    async def run():
        with pytest.raises(module.TelegramTransportError):await t._post('sendMessage',{'text':'fixture'})
    asyncio.run(run())


def test_valid_telegram_response_is_preserved(monkeypatch):
    client_type=httpx.AsyncClient
    body={'ok':True,'result':{'message_id':123}}
    def client(**kw):
        return client_type(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=body)),**kw)
    monkeypatch.setattr(module.httpx,'AsyncClient',client)
    t=module.TelegramTransport(None,bot_token_provider=lambda:'fixture',chat_id_provider=lambda:'1',webhook_secret_provider=lambda:'')
    assert asyncio.run(t._post('sendMessage',{'text':'fixture'}))==body
