"""Offline owner-boundary/replay/STOP checks for the independent Codex channel."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tools.telegram_codex_bridge import Bridge, State, authorized


OWNER = 123456789


def update(text="hello", ident=1):
    return {"update_id": ident, "message": {"from": {"id": OWNER, "is_bot": False},
            "chat": {"id": OWNER, "type": "private"}, "text": text}}


@pytest.fixture
def bot(tmp_path):
    home = tmp_path / "companion"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"people": [{"role": "owner", "user_id": OWNER, "chat_id": OWNER}]}))
    (home / "companion.env").write_text("TG_COMPANION_BOT_TOKEN=123456789:" + "x" * 35)
    b = Bridge(SimpleNamespace(companion_home=home, data_dir=tmp_path / "state", cwd=tmp_path,
                               codex="unused", model="fixture-model"))
    b.thread = "thread-1"
    b.state.put("model", "fixture-model")
    b.send = AsyncMock()
    b.rpc.call = AsyncMock(return_value={"turn": {"id": "turn-1"}})
    b.rpc.write = AsyncMock()
    yield b
    b.state.db.close()
    asyncio.run(b.http.aclose())


@pytest.mark.parametrize("kind", ["other_user", "other_chat", "group", "bot", "string_id", "bool_id",
                                  "forward_origin", "forward_from", "forward_from_chat", "sender_chat",
                                  "edited_message", "channel_post", "callback_query"])
def test_only_numeric_owner_private_new_messages(kind):
    u = update()
    assert authorized(u, OWNER)
    if kind == "other_user": u["message"]["from"]["id"] += 1
    elif kind == "other_chat": u["message"]["chat"]["id"] += 1
    elif kind == "group": u["message"]["chat"]["type"] = "group"
    elif kind == "bot": u["message"]["from"]["is_bot"] = True
    elif kind == "string_id": u["message"]["from"]["id"] = str(OWNER)
    elif kind == "bool_id": u["message"]["from"]["id"] = True
    elif kind in ("edited_message", "channel_post", "callback_query"): u[kind] = {}
    else: u["message"][kind] = {}
    assert not authorized(u, OWNER)


def test_claim_is_durable_no_replay_after_crash(tmp_path):
    s = State(tmp_path)
    assert s.claim(20)
    s.put("paused", True)
    s.db.close()
    s = State(tmp_path)
    assert s.get("paused") is True
    assert not s.claim(20) and not s.claim(19)
    assert s.db.execute("SELECT phase FROM receipts WHERE id=20").fetchone()[0] == "interrupted_unknown"
    s.db.close()


@pytest.mark.asyncio
async def test_duplicate_dispatch_never_calls_codex_twice(bot):
    await bot.handle(update("/codex hello"))
    await asyncio.gather(*bot.background)
    await bot.handle(update("/codex hello"))
    assert bot.rpc.call.await_count == 1


@pytest.mark.asyncio
async def test_stop_before_scheduled_submit_blocks_start_and_survives_resume(bot):
    await bot.handle(update("/codex hello"))
    await bot.handle(update("/stop", 2))
    await asyncio.gather(*bot.background)
    bot.rpc.call.assert_not_awaited()
    assert bot.state.get("paused") is True
    bot.rpc.call.return_value = {"thread": {"status": {"type": "idle"}}}
    await bot.handle(update("/resume", 3))
    assert bot.state.get("paused") is False
    assert bot.rpc.call.call_args.args[0] == "thread/read"
    await bot.handle(update("/codex hello"))  # stale update remains burned
    assert bot.rpc.call.await_count == 1


@pytest.mark.asyncio
async def test_stop_during_start_interrupts_returned_turn(bot):
    started, release = asyncio.Event(), asyncio.Event()
    async def call(method, params):
        if method == "turn/start":
            started.set()
            await release.wait()
            return {"turn": {"id": "turn-race"}}
        return {}
    bot.rpc.call.side_effect = call
    await bot.handle(update("/codex hello"))
    await started.wait()
    await bot.handle(update("/stop", 2))
    release.set()
    await asyncio.gather(*bot.background)
    assert bot.rpc.call.call_args.args == ("turn/interrupt", {"threadId": "thread-1", "turnId": "turn-race"})


@pytest.mark.asyncio
async def test_approval_is_one_shot_and_bound_to_turn(bot):
    bot.turn = "turn-1"
    await bot.event({"id": 50, "method": "item/commandExecution/requestApproval", "params": {
        "threadId": "thread-1", "turnId": "turn-1", "command": "fixture harmless command", "cwd": "fixture"}})
    nonce = next(iter(bot.approvals))
    await bot.handle(update("/approve " + nonce))
    bot.rpc.write.assert_awaited_once_with({"id": 50, "result": {"decision": "accept"}})
    await bot.handle(update("/approve " + nonce, 2))
    assert bot.rpc.write.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["expired", "wrong_turn", "paused", "old_epoch"])
async def test_stale_approval_never_grants(bot, reason):
    import time
    bot.turn = "turn-1"
    bot.approvals["abc"] = {"id": 50, "params": {"turnId": "turn-1"}, "method": "item/commandExecution/requestApproval",
                             "expires": time.monotonic()+10, "epoch": 0}
    if reason == "expired": bot.approvals["abc"]["expires"] = 0
    elif reason == "wrong_turn": bot.turn = "different"
    elif reason == "paused": bot.state.put("paused", True)
    else: bot.epoch += 1
    await bot.handle(update("/approve abc"))
    bot.rpc.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_selection_is_catalog_bound_and_never_changes_permissions(bot):
    bot.models = AsyncMock(return_value=["fixture-model", "other-model"])
    await bot.handle(update("/model injected-model"))
    assert bot.state.get("model") == "fixture-model"
    await bot.handle(update("/model other-model", 2))
    assert bot.state.get("model") == "other-model"
    bot.rpc.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_permissions_request_fails_closed(bot):
    await bot.event({"id": 40, "method": "item/permissions/requestApproval", "params": {
        "threadId": "thread-1", "turnId": "turn-1", "permissions": {"fileSystem": {"write": ["C:/"]}}}})
    assert "error" in bot.rpc.write.call_args.args[0]
    assert not bot.approvals


@pytest.mark.asyncio
async def test_foreign_thread_output_never_forwarded(bot):
    await bot.event({"method": "item/completed", "params": {"threadId": "foreign", "turnId": "other",
        "item": {"id": "foreign-output", "type": "agentMessage", "text": "private"}}})
    bot.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_disconnect_marks_unknown_and_pauses_without_retry(bot):
    bot.rpc.call.side_effect = TimeoutError
    await bot.handle(update("/codex hello"))
    await asyncio.gather(*bot.background)
    assert bot.state.get("paused")
    assert bot.rpc.call.await_count == 1
    assert bot.state.db.execute("SELECT phase FROM receipts WHERE id=1").fetchone()[0] == "dispatch_unknown"


@pytest.mark.asyncio
async def test_resume_cannot_clear_stop_when_runtime_is_still_active(bot):
    bot.state.put("paused", True)
    bot.rpc.call.return_value = {"thread": {"status": {"type": "active"}}}
    await bot.handle(update("/resume"))
    assert bot.state.get("paused") is True


@pytest.mark.asyncio
async def test_delivery_unknown_is_never_silently_retried(bot):
    bot.send = Bridge.send.__get__(bot)
    bot.telegram = AsyncMock(side_effect=TimeoutError)
    assert await bot.send("test", key="same-output") is False
    assert await bot.send("test", key="same-output") is False
    assert bot.telegram.await_count == 1
    assert bot.state.db.execute("SELECT phase FROM deliveries").fetchone()[0] == "delivery_unknown"
