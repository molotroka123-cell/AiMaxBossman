"""pult.report (owner 29.10): an agent can send a TEXT report to the owner's Пульт; recipient fixed, secrets cut, rate-limited."""
import asyncio

import httpx

from bcc.features import pult_report as pr
from bcc.tools import ToolContext


def _ctx():
    return ToolContext(svc=None, task={}, run_id=1, agent={"name": "DeepSeek"})


def _patch(monkeypatch, calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})

    real = pr._send_text

    async def send(token, chat_id, text, proxy=""):
        return await real(token, chat_id, text, proxy, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(pr, "_send_text", send)
    monkeypatch.setattr(pr._pult, "load_target", lambda *a, **k: ("123:ABC", 555, ""))
    pr._sent.clear()


def test_report_is_sent_to_the_fixed_owner_chat_with_secrets_cut(monkeypatch):
    calls = []
    _patch(monkeypatch, calls)
    res = asyncio.run(pr._report({"title": "Итог", "text": "готово, api_key=sk-abcdefghijklmnopqrstuv1234 и ещё"}, _ctx()))
    assert not res.error and res.data["message_id"] == 77
    body = calls[0].content.decode()
    assert "chat_id=555" in body and "sk-abcdefghijkl" not in body and "%5B%D1%81%D0%BA%D1%80%D1%8B%D1%82%D0%BE%5D" in body


def test_empty_text_refused_and_rate_limit(monkeypatch):
    calls = []
    _patch(monkeypatch, calls)
    assert asyncio.run(pr._report({"text": "  "}, _ctx())).error
    for _ in range(pr.MAX_PER_HOUR):
        assert not asyncio.run(pr._report({"text": "ok"}, _ctx())).error
    res = asyncio.run(pr._report({"text": "ok"}, _ctx()))
    assert res.error and "per hour" in res.content and len(calls) == pr.MAX_PER_HOUR


def test_agent_cannot_name_a_recipient():
    spec_args = {"title", "text"}
    import inspect
    src = inspect.getsource(pr)
    assert "chat_id" not in src.split("input_schema=")[1].split("required=")[0]
    assert spec_args == {"title", "text"}
