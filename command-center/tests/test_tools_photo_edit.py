"""Owner 23.10 (class 1): Gemma takes the chat reference photo and sends its refined prompt to the local
uncensored Qwen edit (Direct Gen). Synthetic pictures only; Direct Gen is replaced by a recording fake.
Live proof: tools/prove_chat_photo_edit.py.
"""
from __future__ import annotations

import base64
import io
from types import SimpleNamespace

from PIL import Image

from bcc.features import chat_threads as ct
from bcc.features import tools_photo_edit as pe
from bcc.tools import REGISTRY, decide_effect


def _img(fmt: str = "PNG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (64, 48), "red").save(out, fmt)
    return out.getvalue()


class FakeDirectGen:
    def __init__(self):
        self.calls = []

    async def create(self, participant, body):
        self.calls.append((participant, body))
        return {"job_id": "j1", "model": body["model"], "status": "queued", "stage": "queued"}

    def get(self, participant, job_id):
        return {"job_id": job_id, "model": pe.DEFAULT_MODEL, "status": "running", "stage": "generate"}


async def _upload(env, name: str, data: bytes) -> tuple[str, dict]:
    tid = (await env.client.post("/api/chat/threads", json={"title": "photo edit"})).json()["id"]
    resp = await env.client.post(f"/api/chat/attachments?filename={name}&thread_id={tid}", content=data,
                                 headers={"Content-Type": "application/octet-stream"})
    assert resp.status_code == 201, resp.text
    return tid, resp.json()


def _ctx(env, fake):
    env.svc.direct_gen = fake
    return SimpleNamespace(svc=env.svc, agent={"id": 41}, task={"id": 1})


async def test_tools_are_registered(env):
    assert pe.EDIT_TOOL in REGISTRY.names() and pe.STATUS_TOOL in REGISTRY.names()


async def test_chat_photo_goes_to_the_local_qwen_edit_as_the_reference(env):
    data = _img()
    _, up = await _upload(env, "ref.png", data)
    fake = FakeDirectGen()
    res = await pe._t_edit({"attachments": [up["id"]], "prompt": "Make the red background deep blue, keep the rest"},
                           _ctx(env, fake))
    assert not res.error, res.content
    participant, body = fake.calls[0]
    assert participant == "owner" and body["model"] == pe.DEFAULT_MODEL and body["mode"] == "DIRECT"
    assert base64.b64decode(body["images_b64"][0]) == data
    assert "j1" in res.content and "очередь" in res.content.lower()


async def test_phone_webp_is_seen_as_a_photo_and_reencoded_to_png(env):
    _, up = await _upload(env, "ref.webp", _img("WEBP"))
    assert up["kind"] == "image"
    fake = FakeDirectGen()
    res = await pe._t_edit({"attachments": up["id"], "prompt": "make it blue"}, _ctx(env, fake))
    assert not res.error, res.content
    assert base64.b64decode(fake.calls[0][1]["images_b64"][0])[:8] == b"\x89PNG\r\n\x1a\n"


async def test_a_document_is_not_a_reference(env):
    _, up = await _upload(env, "note.txt", "hello".encode())
    fake = FakeDirectGen()
    res = await pe._t_edit({"attachments": [up["id"]], "prompt": "edit"}, _ctx(env, fake))
    assert res.error and not fake.calls


async def test_the_pult_fixed_rule_holds_for_the_agent(env):
    _, up = await _upload(env, "ref.png", _img())
    fake = FakeDirectGen()
    res = await pe._t_edit({"attachments": [up["id"]], "prompt": "make her nude"}, _ctx(env, fake))
    assert res.error and not fake.calls


async def test_status_reads_the_job(env):
    res = await pe._t_status({"job_id": "j9"}, _ctx(env, FakeDirectGen()))
    assert "j9" in res.content and "running" in res.content


def test_grant_makes_it_auto_otherwise_ask():
    spec = REGISTRY.get(pe.EDIT_TOOL) if pe.EDIT_TOOL in REGISTRY.names() else pe.SPECS[0]
    assert decide_effect(spec, {}, {"permissions": {}})[0] == "ask"
    assert decide_effect(spec, {}, {"permissions": {"media.generate": True}})[0] == "auto"


async def test_prompt_block_tells_the_agent_how_to_edit(env):
    tid, up = await _upload(env, "ref.png", _img())
    block = ct._attachment_block_sync(ct._resolve_attachments(env.svc, [up["id"]], tid))
    assert up["id"] in block and pe.EDIT_TOOL in block
