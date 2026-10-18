"""«Отправить в пульт»: ONE finished file, as a Telegram document, only on request, only to the single owner.

A fake Bot API transport (httpx.MockTransport) stands in for api.telegram.org: nothing leaves the machine.
Negative controls: unfinished job -> 409, two recipients -> refused, non-owner participant -> 403, oversize -> 413,
Telegram/network errors -> 502 without the token anywhere, no automatic delivery after a job completes."""
from __future__ import annotations

import json
import logging

import httpx
import pytest

from bcc.direct_gen import pult

from .test_direct_gen_photo import PNG, FakeSd, create, finish, install

TOKEN = "999999:FAKE_token_for_unit_tests_only_0123456789"          # ci-secret-scan: allow
CHAT = 424242


def companion(tmp_path, *, people=1, role="owner"):
    d = tmp_path / "companion"
    d.mkdir(exist_ok=True)
    cfg = {"enabled": True, "people": [{"user_id": CHAT + i, "chat_id": CHAT + i, "role": role if i == 0 else "guest", "agent_id": 1 + i}
                                       for i in range(people)]}
    (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (d / "companion.env").write_text(f"TG_COMPANION_BOT_TOKEN={TOKEN}\n", encoding="utf-8")
    return d / "config.json"


class BotApi:
    """Records every request; answers like Telegram."""

    def __init__(self, reply=None, exc=None):
        self.requests: list[httpx.Request] = []
        self.reply, self.exc = reply, exc

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.exc:
            raise self.exc
        return httpx.Response(200, json=self.reply or {"ok": True, "result": {"message_id": 77}})


@pytest.fixture(autouse=True)
def _no_env_token(monkeypatch):
    for name in ("TG_COMPANION_BOT_TOKEN", "TG_COMPANION_CORE_TOKEN", "TG_COMPANION_PROXY"):
        monkeypatch.delenv(name, raising=False)


async def done_job(env, tmp_path, bot, *, people=1, role="owner"):
    svc = install(env, tmp_path, FakeSd)
    svc.pult_config = companion(tmp_path, people=people, role=role)
    svc.pult_transport = httpx.MockTransport(bot)
    r = await create(env, model="z-image-turbo", prompt="a lighthouse", resolution="512x768", steps=8)
    job = await finish(env, r.json()["job_id"])
    assert job["status"] == "completed"
    return svc, job


def post(env, job_id, who="owner"):
    return env.client.post(f"/api/direct-gen/jobs/{job_id}/send-to-pult", headers={"X-Participant": who})


async def test_send_uploads_the_original_file_as_a_document_with_the_caption(env, tmp_path, caplog):
    bot = BotApi()
    svc, job = await done_job(env, tmp_path, bot)
    assert bot.requests == [], "completion alone never sends anything"
    caplog.set_level(logging.DEBUG)
    r = await post(env, job["job_id"])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is True and out["sent"] is True and out["message_id"] == 77 and out["bytes"] == len(PNG)
    (req,) = bot.requests
    assert req.url.path.endswith("/sendDocument") and req.url.path.startswith(f"/bot{TOKEN}/")
    body = req.content
    assert PNG in body, "original bytes, not recompressed"
    assert f'name="chat_id"\r\n\r\n{CHAT}'.encode() in body
    assert b"disable_content_type_detection" in body and b'name="document"; filename="bossman-' in body
    cap = out["caption"]
    parts = cap.split(" · ")
    assert parts[0].endswith(" UTC") and len(parts[0]) == 9 and parts[1] == "z-image-turbo" and parts[2] == "512x768"
    assert parts[3] == "8 шагов" and parts[4] == "#" + job["job_id"][:8]
    assert cap.encode() in body
    # secrets stay inside: not in the answer, not in the logs
    assert TOKEN not in r.text and str(CHAT) not in r.text and TOKEN not in caplog.text and str(CHAT) not in caplog.text


async def test_each_tap_sends_again_no_dedupe(env, tmp_path):
    bot = BotApi()
    svc, job = await done_job(env, tmp_path, bot)
    assert (await post(env, job["job_id"])).status_code == 200
    assert (await post(env, job["job_id"])).status_code == 200
    assert len(bot.requests) == 2


async def test_unfinished_job_is_409_and_nothing_is_sent(env, tmp_path):
    bot = BotApi()
    svc = install(env, tmp_path, lambda: FakeSd(hold=True))
    svc.pult_config = companion(tmp_path)
    svc.pult_transport = httpx.MockTransport(bot)
    r = await create(env, model="z-image-turbo", prompt="a lighthouse")
    jid = r.json()["job_id"]
    got = await post(env, jid)
    assert got.status_code == 409 and got.json()["error"]["code"] == "no_result"
    assert bot.requests == []
    unknown = await post(env, "f" * 32)
    assert unknown.status_code == 404
    await env.client.post(f"/api/direct-gen/jobs/{jid}/cancel", headers={"X-Participant": "owner"})


async def test_two_recipients_or_a_non_owner_recipient_is_refused(env, tmp_path):
    bot = BotApi()
    svc, job = await done_job(env, tmp_path, bot, people=2)
    r = await post(env, job["job_id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "pult_recipient_ambiguous"
    assert TOKEN not in r.text and str(CHAT) not in r.text
    svc.pult_config = companion(tmp_path, people=1, role="guest")           # a guest alone is not the owner
    # (a config without an owner is invalid for the companion itself: it surfaces as an unreadable config)
    r = await post(env, job["job_id"])
    assert r.status_code == 409 and r.json()["error"]["code"] in {"pult_recipient_not_owner", "pult_config_invalid"}
    svc.pult_config = tmp_path / "nowhere" / "config.json"
    r = await post(env, job["job_id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "pult_not_configured"
    assert bot.requests == []


async def test_only_the_owner_participant_may_send(env, tmp_path):
    bot = BotApi()
    svc, job = await done_job(env, tmp_path, bot)
    r = await post(env, job["job_id"], who="a" * 64)
    assert r.status_code in (403, 404) and bot.requests == []


async def test_oversize_file_is_refused_before_any_network_call(env, tmp_path, monkeypatch):
    bot = BotApi()
    svc, job = await done_job(env, tmp_path, bot)
    monkeypatch.setattr(pult, "MAX_BYTES", 10)
    r = await post(env, job["job_id"])
    assert r.status_code == 413 and "49 МБ" in r.json()["error"]["message"] and bot.requests == []


async def test_telegram_and_network_errors_never_leak_the_token(env, tmp_path):
    bot = BotApi(reply={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})
    svc, job = await done_job(env, tmp_path, bot)
    r = await post(env, job["job_id"])
    assert r.status_code == 502 and r.json()["error"]["code"] == "pult_rejected" and "400" in r.json()["error"]["message"]
    assert TOKEN not in r.text and str(CHAT) not in r.text
    boom = BotApi(exc=httpx.ConnectError(f"connect failed for https://api.telegram.org/bot{TOKEN}/sendDocument"))
    svc.pult_transport = httpx.MockTransport(boom)
    r = await post(env, job["job_id"])
    assert r.status_code == 502 and r.json()["error"]["code"] == "pult_unreachable"
    assert TOKEN not in r.text and "api.telegram.org" not in r.text


async def test_facelock_result_sends_the_locked_file(env, tmp_path):
    from .test_facelock import fake_worker, with_facefusion, b64
    bot = BotApi()
    svc = install(env, tmp_path, FakeSd)
    with_facefusion(svc, tmp_path)
    svc.facelock_runner = fake_worker("applied")
    svc.pult_config = companion(tmp_path)
    svc.pult_transport = httpx.MockTransport(bot)
    r = await create(env, model="qwen-image-2.1-uc-edit", prompt="x @1", image_b64=b64(), facelock=True)
    job = await finish(env, r.json()["job_id"])
    assert (await post(env, job["job_id"])).status_code == 200
    assert PNG + b"LOCKED" in bot.requests[0].content
