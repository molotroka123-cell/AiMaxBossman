"""SweetDrops sticker flourish: one random sticker after a long reply, never breaks the reply (owner 07.10).

The patch used to live only in the installed build, so every redeploy erased it: it is source now.
"""
import asyncio
from types import SimpleNamespace

import pytest

from bcc.telegram_companion import service
from bcc.telegram_companion.adapters import CompanionError, Telegram


def run(coro):
    return asyncio.run(coro)


class _FakeTelegram:
    def __init__(self, set_result=None, fail_send=False):
        self.calls, self.sent, self._set, self._fail = [], [], set_result, fail_send

    async def call(self, method, payload):
        self.calls.append((method, payload))
        return self._set

    async def send_sticker(self, person, file_id):
        if self._fail:
            raise RuntimeError("network")
        self.sent.append((person, file_id))
        return 1


def _companion(ids=None, tg=None):
    c = object.__new__(service.Companion)
    c._sweet_drops_ids = list(ids or [])
    c.telegram = tg
    return c


PERSON = SimpleNamespace(chat_id=1, role="owner")


def test_a_long_reply_gets_exactly_one_sticker_from_the_pack():
    tg = _FakeTelegram()
    c = _companion(ids=["AAA", "BBB"], tg=tg)
    run(c.maybe_long_message_sticker(PERSON, "x" * service.SWEET_DROPS_MIN_CHARS))
    assert len(tg.sent) == 1 and tg.sent[0][1] in {"AAA", "BBB"}


def test_a_short_reply_gets_no_sticker():
    tg = _FakeTelegram()
    c = _companion(ids=["AAA"], tg=tg)
    run(c.maybe_long_message_sticker(PERSON, "x" * (service.SWEET_DROPS_MIN_CHARS - 1)))
    assert tg.sent == []


def test_the_pack_is_fetched_once_cached_and_a_failure_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    (tmp_path / "Bossman" / "telegram-companion").mkdir(parents=True)   # exists on a real install
    pack = {"stickers": [{"file_id": "F1"}, {"file_id": "F2"}, {"nope": 1}]}
    tg = _FakeTelegram(set_result=pack)
    c = _companion(tg=tg)
    assert run(c._ensure_sweet_drops()) == ["F1", "F2"]
    assert tg.calls == [("getStickerSet", {"name": service.SWEET_DROPS_PACK})]
    c2 = _companion(tg=_FakeTelegram(set_result=None))        # a second process reads the disk cache, no API call
    assert run(c2._ensure_sweet_drops()) == ["F1", "F2"] and c2.telegram.calls == []
    broken = _companion(ids=["AAA"], tg=_FakeTelegram(fail_send=True))
    run(broken.maybe_long_message_sticker(PERSON, "y" * 500))   # must not raise
    run(_companion(tg=None).maybe_long_message_sticker(PERSON, "y" * 500))


def test_the_transport_can_send_stickers_and_refuses_a_bad_file_id():
    assert {"sendSticker", "getStickerSet"} <= set(Telegram.METHODS)
    t = object.__new__(Telegram)
    t._send_locks, t._sent_at = {}, {}
    t.authorize_delivery = lambda person: True
    seen = []

    async def call(method, payload):
        seen.append((method, payload))
        return {"message_id": 7}

    t.call = call
    assert run(t.send_sticker(PERSON, "FILEID")) == 7 and seen[0][0] == "sendSticker"
    for bad in ("", "x" * 257, None):
        with pytest.raises(CompanionError):
            run(t.send_sticker(PERSON, bad))
    t.authorize_delivery = lambda person: False
    with pytest.raises(CompanionError):
        run(t.send_sticker(PERSON, "FILEID"))
