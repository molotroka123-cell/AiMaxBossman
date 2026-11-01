"""Live owner run 31.10, task 866: «Запомни кодовое слово X. Ответь: ок» in a pult chat to an agent without memory tools
failed with action_contract/no_verified_action. In a chat the turn is kept in the chat history, so the request is honoured.

Negatives: the same words OUTSIDE a chat still fail; a chat turn asking for another action (file) still fails.
Synthetic data only (tmp data dir, scripted local model).
"""
from __future__ import annotations

import asyncio
import json

from .conftest import client_for, make_settings, start_app, wait_for
from .test_chat_agent_tool_use_e2e import FAST_ENGINE


class TextModel:
    """OpenAI-compatible stub that always answers with plain text (no tool calls)."""

    def __init__(self, text: str = "ок"):
        self.text = text
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> str:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}/v1"

    async def stop(self) -> None:
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()

    async def _handle(self, reader, writer) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            length = next((int(x.split(b":")[1]) for x in head.split(b"\r\n")
                           if x.lower().startswith(b"content-length:")), 0)
            body = json.loads(await reader.readexactly(length) or b"{}")
            if body.get("stream"):
                writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            payload = json.dumps({"id": "x", "object": "chat.completion", "model": "m", "choices": [{
                "index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": self.text}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1}}).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                         + str(len(payload)).encode() + b"\r\nConnection: close\r\n\r\n" + payload)
            await writer.drain()
        finally:
            writer.close()


async def _run(tmp_path, text: str, *, via_chat: bool) -> dict:
    model = TextModel()
    base = await model.start()
    app, svc = await start_app(make_settings(tmp_path), start_workers=True, engine_options=FAST_ENGINE)
    try:
        async with client_for(app, svc) as client:
            prov = (await client.post("/api/providers", json={"name": "loc", "kind": "openai_compat",
                                                              "base_url": base})).json()
            mod = (await client.post("/api/models", json={"provider_id": prov["id"], "name": "gemma-stub",
                                                          "alias": "gemma-stub"})).json()
            # like live agent 41: an explicit tool list WITHOUT any memory tool (memory.write is never granted to it)
            agent = (await client.post("/api/agents", json={"name": "Gemma", "model_id": mod["id"], "max_steps": 3,
                                                            "tools": ["terminal.status"]})).json()
            if via_chat:
                thread = (await client.post("/api/chat/threads", json={})).json()
                sent = (await client.post(f"/api/chat/threads/{thread['id']}/send", json={
                    "text": text, "agent_id": agent["id"], "client_request_id": "req-remember-01"})).json()
                task_id = sent["task"]["id"]
            else:
                created = (await client.post("/api/tasks", json={"prompt": text, "title": "t",
                                                                 "agent_id": agent["id"], "run_now": True})).json()
                task_id = created["task"]["id"]

            async def done():
                t = (await client.get(f"/api/tasks/{task_id}")).json()["task"]
                return t if t["status"] in {"completed", "failed", "stopped", "blocked", "waiting_approval"} else None

            return await wait_for(done, timeout=20)
    finally:
        await svc.stop()
        await model.stop()


async def test_remember_in_a_chat_is_kept_by_the_chat(tmp_path):
    final = await _run(tmp_path, "Запомни кодовое слово ФИАЛКА-122. Ответь одним словом: ок.", via_chat=True)
    assert final["status"] == "completed", final


async def test_remember_outside_a_chat_still_needs_a_real_memory_action(tmp_path):
    final = await _run(tmp_path, "Запомни кодовое слово ФИАЛКА-122. Ответь одним словом: ок.", via_chat=False)
    assert final["status"] == "failed", final


async def test_other_actions_in_a_chat_are_not_exempted(tmp_path):
    final = await _run(tmp_path, "Запомни это и создай файл notes.txt с текстом привет.", via_chat=True)
    assert final["status"] != "completed", final
