"""Jeff 1.8: progressive Telegram replies (edit in place) through the real worker path.

A fake Telegram records every side effect. The contract: one draft message, rate-limited
edits, ONE final edit, the final reply saved and logged once, and every failure degrades to
the ordinary single send without leaving a duplicate. STOP/cancel keeps working.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from bcc.pit import runtime as rt
from bcc.pit.reply_stream import CURSOR
from bcc.providers import ChatResult
from bcc.telegram_companion.config import CompanionError

from .test_jeff_1_8_stream import StreamingLocal, _local_runtime
from .test_pit_runtime import message, warm


class FakeTelegram:
    def __init__(self, *, fail_edits=False, fail_final=False, with_edit=True):
        self.ops: list[tuple] = []
        self.next_id = 500
        self.fail_edits, self.fail_final = fail_edits, fail_final
        self.authorize_delivery = lambda person: True
        if not with_edit:
            self.edit_message = None

    async def send(self, person, text, **kw):
        self.next_id += 1
        self.ops.append(("send", self.next_id, text, kw.get("parse_mode")))
        return self.next_id

    async def edit_message(self, person, message_id, text, parse_mode=None, final=False):
        if (self.fail_final if final else self.fail_edits):
            raise CompanionError("UPSTREAM_HTTP_ERROR")
        self.ops.append(("edit", message_id, text, parse_mode, final))
        return True

    async def delete_message(self, person, message_id):
        self.ops.append(("delete", message_id))
        return True

    async def close(self):
        return None


CHUNKS = tuple(f"слово{n} " for n in range(30))
FINAL = "".join(CHUNKS).strip()


def run_worker(tmp_path, monkeypatch, telegram, *, text="Расскажи что-нибудь", local=None,
               interval=0.0, first_chars=10, cancel_after=None):
    local = local or StreamingLocal(chunks=CHUNKS, lead=0.01, gap=0.01)
    runtime = _local_runtime(tmp_path, local)
    runtime.stream_edit_interval = interval
    runtime.stream_first_chars = first_chars
    person = runtime.settings.people[0]
    warm(runtime, runtime.vault.key_for_telegram(person.user_id))
    runtime.telegram = telegram
    claims = iter([(501, message(text, user_id=person.user_id, message_id=41))])

    def claim(*_args):
        try:
            return next(claims)
        except StopIteration:
            raise asyncio.CancelledError
    monkeypatch.setattr(runtime.store, "claim", claim)
    finished: list[str] = []
    real_finish = runtime.store.finish

    def finish(update_id, phase):
        finished.append(phase)
        return real_finish(update_id, phase)
    monkeypatch.setattr(runtime.store, "finish", finish)

    async def go():
        task = asyncio.create_task(runtime._worker(person, "chat"))
        if cancel_after is not None:
            await asyncio.sleep(cancel_after)
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    try:
        asyncio.run(asyncio.wait_for(go(), timeout=20))
        deliveries = []
        log = runtime.home / "logs" / "delivery_log.jsonl"
        if log.is_file():
            deliveries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        history = runtime.store.history(person.key)
        return runtime, deliveries, history, (finished[-1] if finished else None)
    finally:
        runtime.store.close()


def test_one_draft_progressive_edits_then_one_final_edit(tmp_path, monkeypatch):
    tg = FakeTelegram()
    _, deliveries, history, phase = run_worker(tmp_path, monkeypatch, tg)
    sends = [op for op in tg.ops if op[0] == "send"]
    edits = [op for op in tg.ops if op[0] == "edit"]
    assert len(sends) == 1 and sends[0][2].endswith(CURSOR) and sends[0][3] is None
    assert len(edits) >= 2, "the draft grows while the model writes"
    live, last = edits[:-1], edits[-1]
    assert all(op[2].endswith(CURSOR) and op[3] is None and op[4] is False for op in live)
    assert last[4] is True and last[3] == "HTML" and last[2] == FINAL and CURSOR not in last[2]
    assert all(op[1] == sends[0][1] for op in edits), "always the same message"
    assert not [op for op in tg.ops if op[0] == "delete"]
    # delivered and saved exactly once, under the draft's message id
    assert len(deliveries) == 1 and phase == "done"
    assert [row["content"] for row in history if row["role"] == "assistant"] == [FINAL]
    assert len(history) == 2


def test_edits_are_rate_limited(tmp_path, monkeypatch):
    tg = FakeTelegram()
    local = StreamingLocal(chunks=CHUNKS, lead=0.0, gap=0.02)
    run_worker(tmp_path, monkeypatch, tg, local=local, interval=0.25)
    live_edits = [op for op in tg.ops if op[0] == "edit" and op[4] is False]
    # 30 chunks * 20 ms = 0.6 s of writing: at most one live edit per 0.25 s plus scheduling slack
    assert 1 <= len(live_edits) <= 4, len(live_edits)


def test_commands_and_non_streaming_adapters_use_the_ordinary_single_send(tmp_path, monkeypatch):
    tg = FakeTelegram()
    run_worker(tmp_path, monkeypatch, tg, text="/help")
    assert [op[0] for op in tg.ops] == ["send"] and tg.ops[0][3] == "HTML"
    plain = FakeTelegram(with_edit=False)
    run_worker(tmp_path / "b", monkeypatch, plain)
    assert [op[0] for op in plain.ops] == ["send"] and plain.ops[0][2] == FINAL


def test_switch_off_by_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_JEFF_TELEGRAM_STREAM", "0")
    tg = FakeTelegram()
    run_worker(tmp_path, monkeypatch, tg)
    assert [op[0] for op in tg.ops] == ["send"] and tg.ops[0][2] == FINAL


def test_failing_preview_edits_never_break_the_reply(tmp_path, monkeypatch):
    tg = FakeTelegram(fail_edits=True)
    _, deliveries, history, phase = run_worker(tmp_path, monkeypatch, tg)
    edits = [op for op in tg.ops if op[0] == "edit"]
    assert len(edits) == 1 and edits[0][4] is True and edits[0][2] == FINAL
    assert len(deliveries) == 1 and phase == "done" and len(history) == 2


def test_failed_final_edit_deletes_the_draft_and_sends_once(tmp_path, monkeypatch):
    tg = FakeTelegram(fail_final=True)
    _, deliveries, history, phase = run_worker(tmp_path, monkeypatch, tg)
    kinds = [op[0] for op in tg.ops]
    assert kinds.count("delete") == 1 and kinds.count("send") == 2
    final_send = [op for op in tg.ops if op[0] == "send"][-1]
    assert final_send[2] == FINAL and final_send[3] == "HTML"
    assert tg.ops[-2][0] == "delete" and tg.ops[-2][1] == [op for op in tg.ops if op[0] == "send"][0][1]
    assert len(deliveries) == 1 and deliveries[0]["reply_message_id"] == final_send[1]
    assert [row["content"] for row in history if row["role"] == "assistant"] == [FINAL]


def test_reply_too_long_for_one_message_is_delivered_the_ordinary_way(tmp_path, monkeypatch):
    # ~5000+ chars; distinct words, because a real answer that loops one sentence is garbage to the Jeff 2.0 model guard
    long_chunks = tuple(f"Абзац {i}: " + " ".join(f"фраза{i}и{j}" for j in range(45)) + ". " for i in range(12))
    tg = FakeTelegram()
    local = StreamingLocal(chunks=long_chunks, lead=0.0, gap=0.0)
    _, deliveries, history, phase = run_worker(tmp_path, monkeypatch, tg, local=local)
    kinds = [op[0] for op in tg.ops]
    assert kinds.count("delete") == 1 and kinds[-1] == "send" and kinds.count("send") == 2
    assert len(deliveries) == 1 and phase == "done"


def test_a_discarded_attempt_is_replaced_in_the_same_draft(tmp_path, monkeypatch):
    class Truncated(StreamingLocal):
        async def chat(self, model, messages, **kw):
            self.calls += 1
            on_delta = kw["on_delta"]
            if self.calls == 1:
                await on_delta("Первый обрывок фразы который никто не увидит в итоге")
                return ChatResult(text="Первый обрывок фразы который никто не увидит в итоге",
                                  finish="length", model=model)
            await on_delta("Итоговый полный ответ модели.")
            return ChatResult(text="Итоговый полный ответ модели.", model=model)
    tg = FakeTelegram()
    _, deliveries, history, _phase = run_worker(tmp_path, monkeypatch, tg, local=Truncated())
    last = [op for op in tg.ops if op[0] == "edit"][-1]
    assert last[2] == "Итоговый полный ответ модели." and last[4] is True
    assert not [op for op in tg.ops if op[0] == "send" and "Итоговый" in op[2] and len(tg.ops) > 3]
    assert len(deliveries) == 1
    assert [row["content"] for row in history if row["role"] == "assistant"] == [
        "Итоговый полный ответ модели."]


def test_stop_mid_stream_sends_no_final_and_records_nothing(tmp_path, monkeypatch):
    tg = FakeTelegram()
    local = StreamingLocal(chunks=("текст ",) * 300, lead=0.0, gap=0.02)
    _, deliveries, history, phase = run_worker(tmp_path, monkeypatch, tg, local=local,
                                               cancel_after=0.5)
    assert phase == "delivery_unknown"
    assert not deliveries and not [row for row in history if row["role"] == "assistant"]
    assert not [op for op in tg.ops if op[0] == "edit" and op[4] is True]


# -- the real transport adapter, against a fake Bot API ------------------------------------------------
def _transport(handler):
    from types import SimpleNamespace

    import httpx

    from bcc.telegram_companion.adapters import Telegram
    from bcc.telegram_companion.config import Person
    person = Person(user_id=101, chat_id=101, role="owner")
    settings = SimpleNamespace(bot_token="test-bot-token", core_token="test-core-token",
                               cloud_token="", local_token="", proxy="", people=(person,))
    return Telegram(settings, transport=httpx.MockTransport(handler)), person


@pytest.fixture
def no_sleep(monkeypatch):
    from bcc.telegram_companion import adapters

    async def instant(_seconds):
        return None
    monkeypatch.setattr(adapters.asyncio, "sleep", instant)


def test_transport_edit_message_scrubs_guards_and_verifies(no_sleep):
    import httpx
    seen = []

    def handler(request):
        seen.append((request.url.path.rsplit("/", 1)[-1], json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 77, "text": "x"}})
    telegram, person = _transport(handler)
    assert asyncio.run(telegram.edit_message(person, 77, "Ответ test-bot-token конец",
                                             parse_mode="HTML", final=True)) is True
    method, payload = seen[0]
    assert method == "editMessageText" and payload["chat_id"] == 101 and payload["message_id"] == 77
    assert "test-bot-token" not in payload["text"] and payload["parse_mode"] == "HTML"


def test_transport_edit_message_refuses_revoked_identity_bad_ids_and_oversize(no_sleep):
    import httpx
    called = []

    def handler(request):
        called.append(1)
        return httpx.Response(200, json={"ok": True, "result": True})
    telegram, person = _transport(handler)
    telegram.authorize_delivery = lambda who: False
    with pytest.raises(CompanionError, match="IDENTITY_REVOKED"):
        asyncio.run(telegram.edit_message(person, 5, "текст"))
    telegram.authorize_delivery = lambda who: True
    with pytest.raises(CompanionError, match="TELEGRAM_MESSAGE_ID_INVALID"):
        asyncio.run(telegram.edit_message(person, 0, "текст"))
    with pytest.raises(CompanionError, match="TELEGRAM_EDIT_TEXT_INVALID"):
        asyncio.run(telegram.edit_message(person, 5, "я" * 4001))
    assert not called


def test_transport_live_edit_gives_up_on_rate_limit_but_final_edit_retries_once(no_sleep):
    import httpx
    from bcc.telegram_companion.adapters import RateLimited
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(200, json={"ok": False, "error_code": 429,
                                             "parameters": {"retry_after": 1}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})
    telegram, person = _transport(handler)
    with pytest.raises(RateLimited):
        asyncio.run(telegram.edit_message(person, 9, "живой текст"))
    attempts.clear()
    assert asyncio.run(telegram.edit_message(person, 9, "итог", final=True)) is True
    assert len(attempts) == 2
