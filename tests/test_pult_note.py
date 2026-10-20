"""tools/pult_note.py: UTF-8 body, owner-only guard, token never echoed, pin. Synthetic config, fake transport."""
from __future__ import annotations

import json

import pytest

from tools import pult_note as pn

TOKEN = "123456:SYNTHETIC-token-for-tests-only"


def companion(cdir, people):
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "companion.env").write_text(f"TG_COMPANION_BOT_TOKEN={TOKEN}\n", encoding="utf-8")
    (cdir / "config.json").write_text(json.dumps({"people": people}), encoding="utf-8")
    return cdir


def fake_post(calls, ok=True):
    def post(url, body):
        calls.append((url, body))
        if url.endswith("/sendMessage"):
            return {"ok": ok, "result": {"message_id": 77}, "description": "bad"}
        return {"ok": True}
    return post


def test_cyrillic_goes_out_as_real_utf8_not_mojibake(tmp_path):
    calls = []
    res = pn.send("💱 Обменник включён — курс ЧНБ", cdir=companion(tmp_path, [{"chat_id": 42}]), post=fake_post(calls))
    assert res == {"message_id": 77, "pinned": None}
    _, body = calls[0]
    assert body.decode("utf-8") == json.dumps({"chat_id": 42, "text": "💱 Обменник включён — курс ЧНБ",
                                              "disable_web_page_preview": True}, ensure_ascii=False)
    assert "РћР±" not in body.decode("utf-8")          # the PowerShell-5 mojibake pattern seen 19.10


@pytest.mark.parametrize("people", [[], [{"chat_id": 1}, {"chat_id": 2}]])
def test_refuses_unless_exactly_one_recipient(tmp_path, people):
    calls = []
    with pytest.raises(pn.PultNoteError, match="ровно один"):
        pn.send("x", cdir=companion(tmp_path, people), post=fake_post(calls))
    assert calls == []


def test_pin_and_telegram_refusal(tmp_path):
    calls = []
    res = pn.send("закрепи", pin=True, cdir=companion(tmp_path, [42]), post=fake_post(calls))
    assert res["pinned"] is True and calls[1][0].endswith("/pinChatMessage")
    with pytest.raises(pn.PultNoteError, match="Telegram отказал"):
        pn.send("x", cdir=companion(tmp_path, [42]), post=fake_post([], ok=False))


def test_empty_and_overlong_text():
    with pytest.raises(pn.PultNoteError, match="пустой"):
        pn.build_payload(1, "   ")
    body = json.loads(pn.build_payload(1, "я" * 5000))
    assert len(body["text"]) == pn.MAX_LEN and body["text"].endswith("…")


def test_dry_run_never_prints_token(tmp_path, monkeypatch, capsys):
    companion(tmp_path / "Bossman" / "telegram-companion", [{"chat_id": 42}])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert pn.main(["--dry-run", "проверка"]) == 0
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err
    assert json.loads(out.out)["would_send_chars"] == len("проверка")
