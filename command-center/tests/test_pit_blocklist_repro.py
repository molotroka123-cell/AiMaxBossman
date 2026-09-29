"""Reproduction: the owner's private Jeff block rule under an open allowlist.

Synthetic IDs only. The owner's live PIT config has allowlist_open=true and a
private block file; the account listed there must never get a Jeff reply.
"""
from __future__ import annotations

import asyncio
import dataclasses

from bcc.pit import runtime as rt
import httpx

from .test_pit_runtime import FakeAdapter, make_settings
from .test_pit_telegram_fake import FakeBotAPI

BLOCKED = 555000111          # synthetic
FRIEND = 101                 # synthetic


def _update(update_id: int, user: int, text: str) -> dict:
    return {"update_id": update_id, "message": {
        "message_id": update_id, "text": text,
        "from": {"id": user, "is_bot": False}, "chat": {"id": user, "type": "private"}}}


def test_open_allowlist_does_not_bypass_the_private_block_file(tmp_path, monkeypatch):
    rule = tmp_path / "private" / "jeff_blocked_telegram_ids.txt"
    rule.parent.mkdir(parents=True)
    rule.write_bytes(f"{BLOCKED}\n".encode("ascii"))
    monkeypatch.setenv("BOSSMAN_PIT_BLOCKED_IDS_FILE", str(rule))
    settings = dataclasses.replace(make_settings(tmp_path), allowlist_open=True)
    api = FakeBotAPI([_update(1, BLOCKED, "/start"), _update(2, BLOCKED, "Привет"),
                      _update(3, FRIEND, "/start")])
    runtime = rt.ParticipantRuntime(settings)
    # Keep the production Telegram adapter (and its delivery authorization);
    # only its HTTP transport is the in-process fake Bot API.
    await_close = runtime.telegram.client
    runtime.telegram.client = httpx.AsyncClient(transport=api, follow_redirects=False,
                                                trust_env=False, timeout=35)
    runtime.adapter = FakeAdapter("ответ")

    async def go():
        async def stop_later():
            # Open-allowlist workers for new guests spawn on a 2 s tick.
            await asyncio.sleep(5)
            (runtime.home / rt.STOP_FLAG).write_text("test", encoding="utf-8")
        timer = asyncio.create_task(stop_later())
        await asyncio.wait_for(runtime.run(), timeout=20)
        await timer
        await await_close.aclose()
    asyncio.run(go())
    assert [row["chat_id"] for row in api.sent] == [FRIEND]
    assert runtime.adapter.calls == []
