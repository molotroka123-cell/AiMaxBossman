"""Owner 23.10: the picture made by the chat agent's `photo.edit` ARRIVES BY ITSELF in the same chat thread (a note
with the picture the mobile pult can show) and in the Telegram pult (the send-to-pult code path) — no hunting in
«Генерации».

No network: Direct Gen runs the scripted FakeSd, Telegram is an httpx.MockTransport (BotApi) or a recorder. Synthetic
pictures and synthetic chat text only (owner rule 21.10). Negative controls: a failed job sends nothing, owner STOP
sends nothing, a restart re-scan delivers exactly once, an interrupted send is not repeated.
"""
from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from bcc.direct_gen import pult
from bcc.features import chat_threads as ct
from bcc.features import tools_photo_edit as pe

from .conftest import client_for, start_app, wait_for
from .test_direct_gen_photo import PNG, FakeSd, create, finish, install
from .test_direct_gen_pult import CHAT, TOKEN, BotApi, companion

TASK_ID = 5


@pytest.fixture(autouse=True)
def _no_env_token(monkeypatch):
    monkeypatch.setenv("BOSSMAN_PHOTO_EDIT_TO_TELEGRAM", "1")     # the send path is under test; off-by-default has its own test
    for name in ("TG_COMPANION_BOT_TOKEN", "TG_COMPANION_CORE_TOKEN", "TG_COMPANION_PROXY"):
        monkeypatch.delenv(name, raising=False)


def _png(w=64, h=48) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), "red").save(out, "PNG")
    return out.getvalue()


def _registry(env) -> Path:
    return Path(env.settings.data_dir) / "chat" / "photo-edit-deliveries.json"


def _entries(env) -> dict:
    try:
        return json.loads(_registry(env).read_text(encoding="utf-8"))["deliveries"]
    except FileNotFoundError:
        return {}


async def _chat_with_photo(env) -> tuple[str, str]:
    """A chat thread whose turn is task TASK_ID (as chat_threads.send_turn writes it) + an attached photo."""
    tid = (await env.client.post("/api/chat/threads", json={"title": "правка фото"})).json()["id"]
    up = await env.client.post(f"/api/chat/attachments?filename=ref.png&thread_id={tid}", content=_png(),
                               headers={"Content-Type": "application/octet-stream"})
    assert up.status_code == 201, up.text
    await ct._append_turn(env.svc, tid, ct._new_turn(TASK_ID, "сделай фон синим", [up.json()]))
    return tid, up.json()["id"]


def _setup_direct_gen(env, tmp_path, bot, **sd):
    svc = install(env, tmp_path, lambda: FakeSd(hold=True, **sd))
    svc.pult_config = companion(tmp_path)
    svc.pult_transport = httpx.MockTransport(bot)
    return svc


async def _edit(env, attachment_id: str):
    ctx = SimpleNamespace(svc=env.svc, agent={"id": 41}, task={"id": TASK_ID})
    res = await pe._t_edit({"attachments": [attachment_id], "prompt": "Make the red background deep blue"}, ctx)
    assert not res.error, res.content
    return res


def _release():
    for sd in FakeSd.instances:
        sd.hold = False


async def _notes(env, tid: str) -> list[dict]:
    got = await env.client.get(f"/api/chat/threads/{tid}")
    assert got.status_code == 200, got.text
    return [t for t in got.json()["turns"] if t.get("kind") == "note"]


async def _wait_note(env, tid: str, timeout: float = 8.0) -> dict:
    async def check():
        notes = await _notes(env, tid)
        return notes[0] if notes else None
    return await wait_for(check, timeout=timeout)


async def test_finished_edit_lands_in_the_same_chat_and_once_in_the_pult(env, tmp_path):
    bot = BotApi()
    _setup_direct_gen(env, tmp_path, bot)
    tid, att = await _chat_with_photo(env)
    res = await _edit(env, att)
    job_id = res.data["job_id"]
    assert res.data["thread_id"] == tid and res.data["auto_delivery"] is True
    assert "придёт сама в этот чат" in res.content
    assert bot.requests == [], "nothing is sent before the picture exists"
    _release()
    turn = await _wait_note(env, tid)
    note = turn["note"]
    assert note["job_id"] == job_id and note["status"] == "completed"
    assert note["file_url"] == f"/api/direct-gen/jobs/{job_id}/file" and note["mime"] == "image/png"
    assert isinstance(note["elapsed_s"], (int, float))
    assert note["pult"]["sent"] is True and note["pult"]["message_id"] == 77
    assert job_id[:8] in turn["text"] and "Пульт" in turn["text"]
    # the URL in the note serves the picture (authenticated like every /api route)
    pic = await env.client.get(note["file_url"], headers={"X-Participant": "owner"})
    assert pic.status_code == 200 and pic.content == PNG
    # exactly one Telegram document, through the send-to-pult path (original bytes, owner chat)
    (req,) = bot.requests
    assert req.url.path == f"/bot{TOKEN}/sendDocument" and PNG in req.content
    assert f'name="chat_id"\r\n\r\n{CHAT}'.encode() in req.content
    assert TOKEN not in json.dumps(note) and str(CHAT) not in json.dumps(note)
    # a re-scan (periodic / after restart) and a repeated event do not deliver again
    from bcc.features import photo_edit_delivery as ped
    assert await ped.rescan(env.svc) == 0
    assert await ped.deliver(env.svc, job_id) is None
    await asyncio.sleep(0.2)
    assert len(bot.requests) == 1 and len(await _notes(env, tid)) == 1
    assert _entries(env)[job_id]["state"] == "delivered"
    # the thread's status is still the last real task, not the note
    summary = (await env.client.get(f"/api/chat/threads/{tid}")).json()["last"]
    assert summary["task_id"] == TASK_ID


async def test_failed_edit_writes_the_reason_and_sends_nothing(env, tmp_path):
    bot = BotApi()
    _setup_direct_gen(env, tmp_path, bot, returncode=1, log=["synthetic engine failure"])
    tid, att = await _chat_with_photo(env)
    job_id = (await _edit(env, att)).data["job_id"]
    _release()
    turn = await _wait_note(env, tid)
    note = turn["note"]
    assert note["job_id"] == job_id and note["status"] == "failed"
    assert "synthetic engine failure" in note["reason"] and "file_url" not in note
    assert "не удалась" in turn["text"] and "synthetic engine failure" in turn["text"]
    await asyncio.sleep(0.2)
    assert bot.requests == [], "a failed job sends nothing to Telegram"


async def test_owner_stop_blocks_the_telegram_send_and_the_thread_says_so(env, tmp_path):
    bot = BotApi()
    _setup_direct_gen(env, tmp_path, bot)
    tid, att = await _chat_with_photo(env)
    job_id = (await _edit(env, att)).data["job_id"]
    stop = Path(env.settings.data_dir) / "computer" / "STOP"
    stop.parent.mkdir(parents=True, exist_ok=True)
    stop.write_text("owner stop", encoding="utf-8")
    try:
        _release()
        turn = await _wait_note(env, tid)
    finally:
        stop.unlink()
    note = turn["note"]
    assert note["job_id"] == job_id and note["status"] == "completed"
    assert note["pult"] == {"sent": False, "code": "owner_stop", "reason": "активен STOP владельца"}
    assert "STOP" in turn["text"]
    await asyncio.sleep(0.2)
    assert bot.requests == [], "STOP: nothing goes to Telegram"


async def test_restart_rescan_delivers_once_and_an_interrupted_send_is_not_repeated(env, tmp_path, monkeypatch):
    sent: list[tuple[str, int]] = []

    async def fake_send(token, chat_id, path, name, caption, mime, **kw):
        sent.append((name, Path(path).stat().st_size))
        return 500 + len(sent)

    monkeypatch.setattr(pult, "load_target", lambda config_path=None: (TOKEN, CHAT, ""))
    monkeypatch.setattr(pult, "send_document", fake_send)
    install(env, tmp_path, FakeSd)
    tid, _att = await _chat_with_photo(env)
    done = [await finish(env, (await create(env, prompt=f"a lighthouse {i}")).json()["job_id"]) for i in range(2)]
    assert all(j["status"] == "completed" for j in done)
    fresh, interrupted = done[0]["job_id"], done[1]["job_id"]
    # the process "dies" with both registered: one never handled, one mid-send (state persisted before the upload)
    for task in [t for t in env.svc._tasks if t.get_name() == "bcc-photo-edit-delivery"]:
        task.cancel()
    _registry(env).parent.mkdir(parents=True, exist_ok=True)
    _registry(env).write_text(json.dumps({"version": 1, "deliveries": {
        fresh: {"job_id": fresh, "task_id": TASK_ID, "thread_id": tid, "state": "pending"},
        interrupted: {"job_id": interrupted, "task_id": TASK_ID, "thread_id": tid, "state": "sending"}}}),
        encoding="utf-8")
    assert sent == []
    for _ in range(2):                                     # two restarts: the second finds nothing left to do
        app2, svc2 = await start_app(env.settings, start_workers=False)
        try:
            async with client_for(app2, svc2) as client2:
                async def delivered():
                    e = json.loads(_registry(env).read_text(encoding="utf-8"))["deliveries"]
                    return all(v["state"] == "delivered" for v in e.values())
                await wait_for(delivered, timeout=8)
                turns = (await client2.get(f"/api/chat/threads/{tid}")).json()["turns"]
        finally:
            await svc2.stop()
    assert len(sent) == 1 and sent[0][1] == len(PNG), "exactly one Telegram send, for the never-handled job"
    notes = {t["note"]["job_id"]: t for t in turns if t.get("kind") == "note"}
    assert set(notes) == {fresh, interrupted}, "one note per job, none duplicated by the second restart"
    assert len([t for t in turns if t.get("kind") == "note"]) == 2
    assert notes[fresh]["note"]["pult"]["sent"] is True
    assert notes[interrupted]["note"]["pult"]["sent"] is None
    assert "повторно не отправляю" in notes[interrupted]["note"]["pult"]["reason"]


async def test_unregistered_jobs_are_never_pushed(env, tmp_path):
    """Completion of a job the owner started from «Генерации» (not via photo.edit) sends nothing (rule 18.10)."""
    bot = BotApi()
    svc = install(env, tmp_path, FakeSd)
    svc.pult_config = companion(tmp_path)
    svc.pult_transport = httpx.MockTransport(bot)
    job = await finish(env, (await create(env)).json()["job_id"])
    assert job["status"] == "completed"
    from bcc.features import photo_edit_delivery as ped
    assert await ped.rescan(env.svc) == 0
    await asyncio.sleep(0.2)
    assert bot.requests == []


async def test_telegram_delivery_is_off_by_default_and_the_chat_still_gets_the_picture(env, tmp_path, monkeypatch):
    monkeypatch.delenv("BOSSMAN_PHOTO_EDIT_TO_TELEGRAM", raising=False)
    bot = BotApi()
    _setup_direct_gen(env, tmp_path, bot)
    tid, att = await _chat_with_photo(env)
    res = await _edit(env, att)
    _release()
    turn = await _wait_note(env, tid)
    note = turn["note"]
    assert note["status"] == "completed" and note["file_url"] == f"/api/direct-gen/jobs/{res.data['job_id']}/file"
    assert note["pult"]["sent"] is False and note["pult"]["code"] == "telegram_off"
    assert bot.requests == [], "nothing may reach Telegram unless the owner switched it on"
