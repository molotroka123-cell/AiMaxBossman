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


# -- HTML formatting (owner 21.10: bold, tap-to-copy code, beautiful — not plain text) -------------------------------
def fake_post_html(calls, fail_parse_once=False):
    state = {"failed": False}

    def post(url, body):
        calls.append((url, json.loads(body)))
        if fail_parse_once and not state["failed"] and json.loads(body).get("parse_mode") == "HTML":
            state["failed"] = True
            return {"ok": False, "description": "Bad Request: can't parse entities: unclosed tag"}
        return {"ok": True, "result": {"message_id": 100 + len(calls)}}
    return post


def test_html_payload_sets_parse_mode_and_plain_stays_unchanged():
    assert json.loads(pn.build_payload(1, "<b>Итог</b>", html=True))["parse_mode"] == "HTML"
    assert "parse_mode" not in json.loads(pn.build_payload(1, "<b>Итог</b>"))


def test_esc_makes_any_value_safe_inside_html():
    assert pn.esc("a<b & c>d") == "a&lt;b &amp; c&gt;d"
    assert pn.strip_tags("<b>Итог</b> &amp; <code>ae248d8f</code>") == "Итог & ae248d8f"


def test_split_html_never_cuts_inside_a_pre_block():
    pre = "<pre><code class=\"language-bash\">" + "\n".join(f"git fetch origin branch-{i}" for i in range(40)) + "</code></pre>"
    text = "<b>Заголовок</b>\n\n" + ("абзац " * 300) + "\n\n" + pre + "\n\n" + ("хвост " * 300)
    chunks = pn.split_html(text, limit=1500)
    assert len(chunks) > 1 and all(len(c) <= 1500 for c in chunks)
    for c in chunks:
        assert c.count("<pre>") == c.count("</pre>")
        assert c.count("<code") == c.count("</code>")
    assert "\n".join(strip for strip in (pn.strip_tags(c) for c in chunks)).count("git fetch origin") == 40


def test_oversized_pre_is_split_into_valid_pre_pieces():
    pre = "<pre>" + "\n".join("x" * 50 for _ in range(200)) + "</pre>"
    chunks = pn.split_html(pre, limit=1000)
    assert len(chunks) > 1 and all(c.startswith("<pre>") and c.endswith("</pre>") and len(c) <= 1000 for c in chunks)


def test_long_html_goes_out_as_several_messages_to_the_one_owner(tmp_path):
    calls = []
    res = pn.send("<b>Отчёт</b>\n\n" + "\n\n".join("строка " * 100 for _ in range(20)), html=True,
                  cdir=companion(tmp_path, [{"chat_id": 42}]), post=fake_post_html(calls))
    assert len(res["message_ids"]) == len(calls) > 1
    assert all(body["chat_id"] == 42 and body["parse_mode"] == "HTML" and len(body["text"]) <= pn.MAX_LEN
               for _, body in calls)


def test_broken_markup_falls_back_to_plain_text_instead_of_losing_the_report(tmp_path):
    calls = []
    res = pn.send("<b>Итог <i>без закрытия", html=True, cdir=companion(tmp_path, [42]),
                  post=fake_post_html(calls, fail_parse_once=True))
    assert res["message_id"] and len(calls) == 2
    assert "parse_mode" not in calls[1][1] and calls[1][1]["text"] == "Итог без закрытия"


def test_document_goes_to_the_owner_with_html_caption(tmp_path):
    f = tmp_path / "prompt.md"
    f.write_text("# Промт\n", encoding="utf-8")
    seen = []

    def post(url, body, ctype):
        seen.append((url, body, ctype))
        return {"ok": True, "result": {"message_id": 9}}
    res = pn.send_document(f, "<b>Мастер-промт</b> <code>docs/x</code>", cdir=companion(tmp_path / "c", [{"chat_id": 42}]),
                           post=post)
    url, body, ctype = seen[0]
    assert res == {"message_id": 9} and url.endswith("/sendDocument") and ctype.startswith("multipart/form-data")
    text = body.decode("utf-8")
    assert 'name="chat_id"\r\n\r\n42\r\n' in text and 'name="parse_mode"\r\n\r\nHTML' in text
    assert "<b>Мастер-промт</b>" in text and 'filename="prompt.md"' in text and TOKEN not in text


def test_document_refuses_without_exactly_one_recipient(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(pn.PultNoteError, match="ровно один"):
        pn.send_document(f, "x", cdir=companion(tmp_path / "c", [1, 2]), post=lambda *a: {"ok": True})


def test_overlong_caption_is_cut_as_plain_text(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("plain", encoding="utf-8")
    body, _ = pn.build_document(1, f, "<b>" + "я" * 2000 + "</b>")
    text = body.decode("utf-8")
    assert "parse_mode" not in text and "<b>" not in text and "я" * 1000 in text
