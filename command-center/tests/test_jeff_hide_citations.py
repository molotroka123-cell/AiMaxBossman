"""«Отсылки» (owner 22.10: «пусть теперь уберёт отсылки»): Jeff's strict answers carry no «(§N)» marks unless the owner
switches «показывать ссылки» on. The model still has to cite - an uncited answer is still replaced by «В методичке этого
нет.» - and the cited sections stay visible to the owner in «Проверка». Synthetic manual and fake models only; nothing here
reads the owner's real manual or chats."""
from __future__ import annotations

import asyncio
import base64
import json
import random

import pytest

from bcc.pit import jeff_kb as kb
from bcc.pit.config import pit_home
from bcc.pit.reply_stream import reply_sink
from bcc.providers import ChatResult

from .test_jeff_manual_kb import MANUAL_MD, OWNER, FakeLLM, _ask, _runtime, _upload
from .test_jeff_settings_overlay import pit_setup  # noqa: F401  (fixture)
from .test_pit_runtime import message, warm

SECTIONS = [{"n": 1, "title": "Разминка", "text": "x"}, {"n": 2, "title": "Ставки", "text": "y"}]
QUESTION = "Сколько минут длится разминка перед игрой?"


# ------------------------------------------------------------------------------------------------- pure functions

@pytest.mark.parametrize("raw,clean", [
    ("Разминка длится двенадцать минут (§1).", "Разминка длится двенадцать минут."),
    ("Сначала разминка (§1), потом работа (§2, §3). Итог (см. §2) такой.", "Сначала разминка, потом работа. Итог такой."),
    ("- пункт один (§4)\n- пункт два [§5]\n- без ссылок", "- пункт один\n- пункт два\n- без ссылок"),
    ("Скобки (не ссылка) остаются (§2)", "Скобки (не ссылка) остаются"),
    ("Без ссылок вообще", "Без ссылок вообще"),
    ("(§1)", ""),
])
def test_strip_citations(raw, clean):
    assert kb.strip_citations(raw) == clean
    assert kb.strip_citations(kb.strip_citations(raw)) == clean                 # idempotent


def test_finalize_keeps_legacy_behaviour_and_hides_marks_on_request():
    text = "Двенадцать минут (§1). Ставка от пяти фишек (§2). Чужое (§7)."
    shown, cited = kb.finalize_answer(text, SECTIONS)
    assert "(§1)" in shown and "(§7)" not in shown and cited == [1, 2]            # default call: exactly as before
    hidden, cited_hidden = kb.finalize_answer(text, SECTIONS, hide_citations=True)
    assert hidden == "Двенадцать минут. Ставка от пяти фишек. Чужое." and cited_hidden == [1, 2]


def test_hidden_mode_still_demands_a_citation_and_never_returns_an_empty_answer():
    assert kb.finalize_answer("Разминка длится полчаса.", SECTIONS, hide_citations=True) == (kb.NO_ANSWER_RU, [])
    assert kb.finalize_answer("В методичке этого нет (§1).", SECTIONS, hide_citations=True) == (kb.NO_ANSWER_RU, [])
    assert kb.finalize_answer("(§1)", SECTIONS, hide_citations=True) == (kb.NO_ANSWER_RU, [])
    assert kb.finalize_answer("Только чужое (§9).", SECTIONS, hide_citations=True) == (kb.NO_ANSWER_RU, [])


def test_rules_still_ask_for_citations_but_forbid_other_references():
    assert "(§3)" in kb.STRICT_RULES_RU and "нигде не ссылайся" in kb.STRICT_RULES_RU


# --------------------------------------------------------------------------------------------- live draft filter

def _feed(text, rnd):
    out = []

    async def sink(piece):
        out.append(piece)

    hider = kb.CitationHider(sink)

    async def go():
        i = 0
        while i < len(text):
            n = rnd.randint(1, 7)
            await hider.on_delta(text[i:i + n])
            i += n

    asyncio.run(go())
    return out


def test_draft_filter_never_shows_a_mark_whatever_the_chunking():
    full = ("Разминка длится двенадцать минут (§1), затем отдых (§2, §3). Это всё [§2] и (см. §3) тоже, "
            "(обычная скобка) и ещё слово.")
    final = kb.strip_citations(full)
    for seed in range(200):
        shown = "".join(p for p in _feed(full, random.Random(seed)) if p)
        assert "§" not in shown and "(см" not in shown, (seed, shown)
        assert final.startswith(shown), (seed, shown)                              # nothing is ever taken back
        assert shown == final, (seed, shown)                                       # and nothing is lost at the end


def test_draft_filter_holds_a_half_typed_mark_and_releases_an_ordinary_bracket():
    out = []

    async def sink(piece):
        out.append(piece)

    async def go():
        hider = kb.CitationHider(sink)
        await hider.on_delta("Ответ ")
        await hider.on_delta("(§")
        assert "".join(p for p in out if p) == "Ответ"                              # the open mark is held back
        await hider.on_delta("1)")
        await hider.on_delta(" и (например) всё")
        await hider.on_delta(None)                                                 # a failed attempt: forwarded as a reset
        assert out[-1] is None
        await hider.on_delta("Новый ответ (§2)")
        return "".join(p for p in out[out.index(None) + 1:] if p)

    assert asyncio.run(go()) == "Новый ответ"


# ------------------------------------------------------------------------------------------ state, audit, switch

def test_switch_is_off_by_default_persisted_audited_and_ignores_junk(tmp_path):
    assert kb.default_state()["show_citations"] is False and kb.read_state(tmp_path)["show_citations"] is False
    _upload(tmp_path)
    assert kb.info(tmp_path)["show_citations"] is False
    assert kb.set_mode(tmp_path, show_citations=True)["show_citations"] is True
    assert kb.read_state(tmp_path)["show_citations"] is True
    assert kb.set_mode(tmp_path, strict=True)["show_citations"] is True            # other switches do not reset it
    assert kb.set_mode(tmp_path, show_citations=False)["show_citations"] is False
    (kb.kb_dir(tmp_path) / kb.STATE_FILE).write_text(json.dumps({"show_citations": "yes"}), encoding="utf-8")
    assert kb.read_state(tmp_path)["show_citations"] is False                      # only an explicit true shows the marks
    rows = [json.loads(line) for line in (pit_home(tmp_path) / "logs" / "jeff_kb.jsonl").read_text("utf-8").splitlines()]
    modes = [r for r in rows if r["event"] == "mode"]
    assert [r["show_citations"] for r in modes] == [True, True, False] and all(isinstance(r["show_citations"], bool) for r in modes)


def test_check_answer_hides_marks_but_reports_the_cited_sections(tmp_path):
    _upload(tmp_path)
    manual = kb.load_manual(tmp_path)

    async def chat(messages):
        return "Разминка длится двенадцать минут (§1)."

    hidden = asyncio.run(kb.check_answer(chat, QUESTION, manual=manual, hide_citations=True))
    shown = asyncio.run(kb.check_answer(chat, QUESTION, manual=manual))
    assert hidden["answer"] == "Разминка длится двенадцать минут." and shown["answer"] == "Разминка длится двенадцать минут (§1)."
    assert hidden["cited"] == shown["cited"] == [{"n": 1, "title": "Разминка"}]


# ------------------------------------------------------------------------------------------------- Jeff chat turn

class StreamingLLM(FakeLLM):
    """Like FakeLLM, but types the answer into the live draft in small pieces."""

    async def chat(self, model, messages, **kw):
        self.calls.append((model, [dict(m) for m in messages]))
        sink = kw.get("on_delta")
        if sink is not None:
            for i in range(0, len(self.text), 4):
                await sink(self.text[i:i + 4])
        return ChatResult(text=self.text, model=model)


def _ask_with_draft(runtime, user_id, text, message_id=1):
    person = next(p for p in runtime.settings.people if p.user_id == user_id)
    warm(runtime, runtime.vault.key_for_telegram(user_id))
    drafts = []

    async def sink(piece):
        drafts.append(piece)

    async def go():
        reply_sink.set(sink)
        return await runtime.handle(person, message(text, user_id=user_id, message_id=message_id))

    return asyncio.run(go()), drafts


def test_chat_turn_answers_without_marks_by_default_and_with_them_when_switched_on(tmp_path):
    llm = FakeLLM("Разминка длится двенадцать минут (§1).")
    runtime = _runtime(tmp_path, llm)
    try:
        data_dir = runtime.vault.data_dir
        _upload(data_dir)
        kb.set_mode(data_dir, strict=True)
        hidden = _ask(runtime, OWNER, QUESTION)
        assert "(§" not in hidden and "§" not in hidden and "двенадцать" in hidden
        kb.set_mode(data_dir, show_citations=True)
        assert "(§1)" in _ask(runtime, OWNER, QUESTION, 2)
        rows = [json.loads(line) for line in (pit_home(data_dir) / "logs" / "jeff_kb.jsonl").read_text("utf-8").splitlines()]
        answers = [r for r in rows if r["event"] == "strict_answer"]
        assert [r["cited"] for r in answers] == [1, 1] and not any(r["no_answer"] for r in answers)   # grounded either way
    finally:
        asyncio.run(runtime.close())


def test_chat_turn_uncited_answer_is_still_replaced_when_marks_are_hidden(tmp_path):
    runtime = _runtime(tmp_path, FakeLLM("Разминка длится полчаса, так все делают."))
    try:
        _upload(runtime.vault.data_dir)
        kb.set_mode(runtime.vault.data_dir, strict=True)
        assert _ask(runtime, OWNER, QUESTION) == kb.NO_ANSWER_RU
    finally:
        asyncio.run(runtime.close())


def test_live_draft_of_a_strict_turn_carries_no_marks_unless_switched_on(tmp_path):
    runtime = _runtime(tmp_path, StreamingLLM("Разминка длится двенадцать минут (§1)."))
    try:
        data_dir = runtime.vault.data_dir
        _upload(data_dir)
        kb.set_mode(data_dir, strict=True)
        _answer, drafts = _ask_with_draft(runtime, OWNER, QUESTION)
        shown = "".join(p for p in drafts if p)
        assert shown and "двенадцать" in shown and "§" not in shown, drafts
        kb.set_mode(data_dir, show_citations=True)
        _answer, drafts = _ask_with_draft(runtime, OWNER, QUESTION, 2)
        assert "(§1)" in "".join(p for p in drafts if p)
    finally:
        asyncio.run(runtime.close())


# ------------------------------------------------------------------------------------------------------ owner API

async def test_api_switch_and_check_follow_it(env, pit_setup, monkeypatch):
    from bcc.features import jeff_kb as feature

    def fake_factory(url, model):
        async def chat(messages):
            return "Двенадцать минут (§1)."
        return chat

    monkeypatch.setattr(feature, "chat_factory", fake_factory)
    c = env.client
    data = base64.b64encode(MANUAL_MD.encode("utf-8")).decode()
    assert (await c.put("/api/jeff-kb/manual", json={"name": "rules.md", "data_b64": data})).status_code == 200
    assert (await c.get("/api/jeff-kb")).json()["show_citations"] is False
    hidden = (await c.post("/api/jeff-kb/check", json={"question": "Сколько длится разминка?"})).json()
    assert hidden["answer"] == "Двенадцать минут." and hidden["cited"] == [{"n": 1, "title": "Разминка"}]
    r = await c.put("/api/jeff-kb/mode", json={"show_citations": True})
    assert r.status_code == 200 and r.json()["show_citations"] is True
    shown = (await c.post("/api/jeff-kb/check", json={"question": "Сколько длится разминка?"})).json()
    assert shown["answer"] == "Двенадцать минут (§1)." and shown["cited"] == hidden["cited"]
    assert (await c.put("/api/jeff-kb/mode", json={"show_citations": "maybe"})).status_code == 422
    assert (await c.put("/api/jeff-kb/mode", json={"show_citations": False})).json()["show_citations"] is False


# ------------------------------------------------------------------------------------------------ cloud fallback

def test_cloud_fallback_answer_is_clean_too_and_keeps_the_owner_provenance_line(tmp_path):
    from bcc.providers import ProviderError
    from .test_jeff_manual_cloud_fallback import FREE, DownLLM, RouteFake, _strict_runtime
    llm = DownLLM(ProviderError("127.0.0.1 не ответил за 30 с", kind="network", timed_out=True))
    route = RouteFake("Разминка длится двенадцать минут (§1).")
    runtime = _strict_runtime(tmp_path, llm, route, show_citations=False)
    try:
        answer = _ask(runtime, OWNER, QUESTION)
        assert answer.startswith("Разминка длится двенадцать минут.") and "§" not in answer
        assert answer.endswith("☁️ облако · " + FREE)                               # provenance for the owner is not a citation
        assert len(route.calls) == 1
    finally:
        asyncio.run(runtime.close())
