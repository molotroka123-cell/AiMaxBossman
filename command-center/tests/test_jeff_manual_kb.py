"""Owner's manual («методичка») as Jeff's knowledge base + «строго по методичке» + Jeff's local model choice.

Fakes only: a fake local LLM adapter, a fake chat factory for the owner check, synthetic manual text. No Ollama, no
GPU, no network.
"""
from __future__ import annotations

import asyncio
import base64
import dataclasses
import json

import httpx
import pytest

from bcc.pit import jeff_kb as kb
from bcc.pit import jeff_model as jmod
from bcc.pit.config import Person, pit_home
from bcc.providers import ChatResult

from .test_jeff_settings_overlay import pit_setup  # noqa: F401  (fixture)
from .test_pit_runtime import make_runtime, make_settings, message, warm

NATIVE = "http://127.0.0.1:11434/v1"
MARK = "Синтетика-ZX81"            # a marker that must never appear in a log, a state file or a report
MANUAL_MD = f"""# Разминка
Перед игрой разминка длится ровно двенадцать минут, {MARK}.

# Ставки
Минимальная ставка за столом новичков — пять фишек.
Максимальная ставка — пятьдесят фишек.

# Перерывы
После каждых сорока пяти минут игры положен перерыв на десять минут.
"""


class FakeLLM:
    """Local model stand-in: records every prompt, answers with a scripted text."""

    def __init__(self, text="Разминка длится двенадцать минут (§1)."):
        self.text, self.calls = text, []
        self.models = ["local:m", jmod.GEMMA_LOCAL]

    async def chat(self, model, messages, **kw):
        self.calls.append((model, [dict(m) for m in messages]))
        return ChatResult(text=self.text, model=model)

    async def unload(self, model):
        return None

    async def list_model_info(self):
        return [{"id": m} for m in self.models]


class CloudSpy:
    def __init__(self):
        self.calls = []

    async def chat(self, model, messages, **kw):
        self.calls.append(model)
        return ChatResult(text="облако", model=model)

    async def list_model_info(self):
        return []

    async def list_model_pricing(self):
        return {}

    async def close(self):
        return None


OWNER, GUEST = 101, 202


def _runtime(tmp_path, llm):
    people = (Person(user_id=OWNER, chat_id=OWNER, role="owner"), Person(user_id=GUEST, chat_id=GUEST, role="guest"))
    settings = dataclasses.replace(make_settings(tmp_path, people=people), chat_models=(), local_url=NATIVE,
                                   local_models=("local:m",), local_chat_only=True)
    runtime = make_runtime(tmp_path, settings=settings)
    runtime.local_adapter = llm
    runtime.adapter = CloudSpy()
    runtime.local_settle_seconds = 0.0

    async def allowed():
        return True
    runtime.capacity_guard.local_allowed = allowed
    return runtime


def _ask(runtime, user_id, text, message_id=1):
    person = next(p for p in runtime.settings.people if p.user_id == user_id)
    warm(runtime, runtime.vault.key_for_telegram(user_id))
    return asyncio.run(runtime.handle(person, message(text, user_id=user_id, message_id=message_id)))


def _upload(data_dir, text=MANUAL_MD, name="manual.md"):
    return kb.save_manual(data_dir, name, text.encode("utf-8"))


def _all_text_under(path):
    out = []
    for f in path.rglob("*"):
        if f.is_file():
            try:
                out.append(f.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return "\n".join(out)


# ------------------------------------------------------------------------------------- upload -> sections
def test_upload_splits_into_numbered_sections_and_stores_only_ciphertext(tmp_path):
    info = _upload(tmp_path)
    assert info["present"] and info["readable"] and info["sections"] == 3 and info["strict"] is False
    assert info["scope"] == "owner" and info["size_bytes"] == len(MANUAL_MD.encode("utf-8"))
    titles = kb.section_list(tmp_path)
    assert [t["n"] for t in titles] == [1, 2, 3]
    assert [t["title"] for t in titles] == ["Разминка", "Ставки", "Перерывы"]
    manual = kb.load_manual(tmp_path)
    assert MARK in manual["sections"][0]["text"] and "пять фишек" in manual["sections"][1]["text"]
    # at rest: encrypted manual, a state file and an audit log of numbers only
    on_disk = _all_text_under(pit_home(tmp_path))
    assert MARK not in on_disk and "фишек" not in on_disk and "manual.md" not in on_disk
    log = (pit_home(tmp_path) / "logs" / "jeff_kb.jsonl").read_text(encoding="utf-8")
    row = json.loads(log.splitlines()[-1])
    assert row["event"] == "manual_saved" and row["sections"] == 3 and len(row["sha256"]) == 16


def test_headingless_text_is_cut_into_paragraph_sections_and_numbered_headings_work(tmp_path):
    plain = "\n\n".join(f"Абзац номер {i}. " + "слово " * 120 for i in range(6))
    secs = kb.split_sections(plain)
    assert len(secs) >= 2 and [s["n"] for s in secs] == list(range(1, len(secs) + 1))
    assert all(len(s["text"]) <= kb.SECTION_CHARS for s in secs)
    numbered = kb.split_sections("1. Введение\nтекст один\n2. Правила игры\nтекст два\nПРИЛОЖЕНИЕ А\nтекст три")
    assert [s["title"] for s in numbered] == ["1. Введение", "2. Правила игры", "ПРИЛОЖЕНИЕ А"]


def test_pdf_goes_through_the_hardened_extractor_and_bad_files_are_refused(tmp_path):
    seen = []

    def fake_extract(name, data):
        seen.append(name)
        return [("page=1", "# Страница\nТекст из PDF про ставки.")]
    info = kb.save_manual(tmp_path, "book.PDF", b"%PDF-1.4 fake", extractor=fake_extract)
    assert seen == ["manual.pdf"] and info["sections"] == 1 and info["format"] == "pdf"
    for name, data in (("x.exe", b"MZ"), ("x.txt", b""), ("x.txt", b" \n\t "),
                       ("x.md", b"a" * (kb.MAX_UPLOAD_BYTES + 1))):
        with pytest.raises(kb.KBError):
            kb.save_manual(tmp_path, name, data)
    assert kb.load_manual(tmp_path)["sections"][0]["title"] == "Страница", "a refused upload keeps the old manual"


def test_replace_and_delete(tmp_path):
    _upload(tmp_path)
    kb.set_mode(tmp_path, strict=True)
    _upload(tmp_path, "# Одно\nНовая методичка.", "v2.txt")
    assert kb.info(tmp_path)["sections"] == 1 and kb.info(tmp_path)["strict"] is True
    assert kb.delete_manual(tmp_path) is True
    state = kb.info(tmp_path)
    assert not state["present"] and state["strict"] is False and kb.load_manual(tmp_path) is None
    with pytest.raises(kb.KBError):
        kb.set_mode(tmp_path, strict=True)             # nothing to be strict about


def test_tampered_or_foreign_key_manual_means_no_manual(tmp_path):
    _upload(tmp_path)
    kb.set_mode(tmp_path, strict=True)
    path = kb.kb_dir(tmp_path) / kb.MANUAL_FILE
    path.write_text("not-a-fernet-token", encoding="utf-8")
    assert kb.load_manual(tmp_path) is None
    assert kb.strict_applies(tmp_path, owner_chat=True) is False


# ------------------------------------------------------------------------------------- retrieval / citations
def test_retrieve_finds_the_right_section_and_nothing_for_an_unrelated_question(tmp_path):
    _upload(tmp_path)
    manual = kb.load_manual(tmp_path)
    assert [s["n"] for s in kb.retrieve(manual, "Сколько длится разминка?")] == [1]
    assert 2 in [s["n"] for s in kb.retrieve(manual, "какая минимальная ставка")]
    assert kb.retrieve(manual, "столица Франции") == []


def test_citations_parsed_unknown_ones_removed_and_uncited_answer_becomes_no_answer():
    secs = [{"n": 1, "title": "a", "text": "x"}, {"n": 3, "title": "b", "text": "y"}]
    assert kb.parse_citations("см. (§3) и §1, ещё §3 и § 12") == [3, 1, 12]
    answer, cited = kb.finalize_answer("Двенадцать минут (§1), иначе (§9).", secs)
    assert cited == [1] and "§9" not in answer and "(§1)" in answer
    assert kb.finalize_answer("Двенадцать минут.", secs) == (kb.NO_ANSWER_RU, [])
    assert kb.finalize_answer("Увы, в методичке этого нет (§1).", secs) == (kb.NO_ANSWER_RU, [])
    assert kb.finalize_answer("Ответ (§7).", secs) == (kb.NO_ANSWER_RU, [])


# ------------------------------------------------------------------------------------- strict mode in Jeff
def test_strict_prompt_contains_retrieved_sections_rules_and_only_the_local_model_sees_it(tmp_path):
    llm = FakeLLM()
    runtime = _runtime(tmp_path, llm)
    try:
        _upload(runtime.vault.data_dir)
        kb.set_mode(runtime.vault.data_dir, strict=True)
        answer = _ask(runtime, OWNER, "Сколько минут длится разминка перед игрой?")
        assert "(§1)" in answer and "двенадцать" in answer
        model, messages = llm.calls[-1]
        assert model == "local:m"
        assert kb.STRICT_RULES_RU in messages[0]["content"]
        blocks = [m["content"] for m in messages if m["role"] == "system"]
        sections = next(b for b in blocks if b.startswith("Разделы методички"))
        assert "§1. Разминка" in sections and MARK in sections
        assert "Ставки" not in sections, "only the retrieved sections, not the whole manual"
        assert runtime.adapter.calls == [], "the manual never reaches a cloud route"
        logs = _all_text_under(pit_home(runtime.vault.data_dir) / "logs")
        assert MARK not in logs and "разминка" not in logs.casefold()
    finally:
        asyncio.run(runtime.close())


def test_no_match_answers_no_answer_without_calling_the_model(tmp_path):
    llm = FakeLLM()
    runtime = _runtime(tmp_path, llm)
    try:
        _upload(runtime.vault.data_dir)
        kb.set_mode(runtime.vault.data_dir, strict=True)
        assert _ask(runtime, OWNER, "Какая столица Франции?") == kb.NO_ANSWER_RU
        assert llm.calls == []
    finally:
        asyncio.run(runtime.close())


def test_model_that_invents_without_citation_is_replaced_by_no_answer(tmp_path):
    llm = FakeLLM("Разминка длится полчаса, так все делают.")
    runtime = _runtime(tmp_path, llm)
    try:
        _upload(runtime.vault.data_dir)
        kb.set_mode(runtime.vault.data_dir, strict=True)
        assert _ask(runtime, OWNER, "Сколько длится разминка?") == kb.NO_ANSWER_RU
        assert llm.calls, "the model was asked (a section matched) but its uncited answer is not shown"
    finally:
        asyncio.run(runtime.close())


def test_scope_is_the_owner_chat_by_default_and_everyone_only_with_the_second_switch(tmp_path):
    llm = FakeLLM("Обычный ответ без методички.")
    runtime = _runtime(tmp_path, llm)
    try:
        _upload(runtime.vault.data_dir)
        kb.set_mode(runtime.vault.data_dir, strict=True)
        assert kb.info(runtime.vault.data_dir)["scope_all"] is False
        answer = _ask(runtime, GUEST, "Сколько длится разминка?")
        assert answer != kb.NO_ANSWER_RU and "Обычный ответ" in answer
        _model, messages = llm.calls[-1]
        assert all(kb.STRICT_RULES_RU not in str(m["content"]) for m in messages)
        assert all(MARK not in str(m["content"]) for m in messages), "a participant never gets the manual by default"
        kb.set_mode(runtime.vault.data_dir, scope_all=True)
        assert _ask(runtime, GUEST, "Сколько длится разминка?", 2) == kb.NO_ANSWER_RU   # strict now: uncited
        assert kb.STRICT_RULES_RU in llm.calls[-1][1][0]["content"]
        assert kb.strict_applies(runtime.vault.data_dir, owner_chat=False) is True
    finally:
        asyncio.run(runtime.close())


def test_strict_off_means_jeff_unchanged(tmp_path):
    llm = FakeLLM("Привет!")
    runtime = _runtime(tmp_path, llm)
    try:
        _upload(runtime.vault.data_dir)                   # uploaded but strict mode not switched on
        assert _ask(runtime, OWNER, "Сколько длится разминка?") == "Привет!"
        assert all(MARK not in str(m["content"]) for m in llm.calls[-1][1])
    finally:
        asyncio.run(runtime.close())


# ------------------------------------------------------------------------------------- model choice
def test_model_choice_switches_the_local_model_per_turn_and_default_is_unchanged(tmp_path):
    llm = FakeLLM("Ок.")
    runtime = _runtime(tmp_path, llm)
    try:
        _ask(runtime, OWNER, "Расскажи что-нибудь", 1)
        assert llm.calls[-1][0] == "local:m", "no choice file: the configured model"
        jmod.set_override(runtime.vault.data_dir, jmod.GEMMA_LOCAL)
        _ask(runtime, OWNER, "Расскажи ещё", 2)
        assert llm.calls[-1][0] == jmod.GEMMA_LOCAL
        assert runtime.settings.local_url == NATIVE and runtime.settings.chat_models == ()
        jmod.clear_override(runtime.vault.data_dir)
        _ask(runtime, OWNER, "И ещё", 3)
        assert llm.calls[-1][0] == "local:m"
    finally:
        asyncio.run(runtime.close())


def test_model_choice_refuses_banned_or_malformed_ids_and_ignores_a_broken_file(tmp_path):
    for bad in ("liquid/lfm-2", "LFM2-8b", "", "a b", "x" * 200, "../etc"):
        with pytest.raises(jmod.ModelChoiceError):
            jmod.set_override(tmp_path, bad)
    jmod.model_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    jmod.model_path(tmp_path).write_text(json.dumps({"local_model": "lfm-evil"}), encoding="utf-8")
    assert jmod.read_override(tmp_path) == ""
    assert jmod.status(tmp_path, ["bossman-uncensored-chat:latest"])["effective"] == "bossman-uncensored-chat:latest"


def test_model_cli_sets_gemma_by_default(tmp_path, capsys):
    assert jmod.main(["set", "--data-dir", str(tmp_path)]) == 0
    assert jmod.read_override(tmp_path) == "gemma4-31b-uncensored:latest"
    assert jmod.main(["status", "--data-dir", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["effective"] == "gemma4-31b-uncensored:latest"
    assert jmod.main(["clear", "--data-dir", str(tmp_path)]) == 0 and jmod.read_override(tmp_path) == ""


# ------------------------------------------------------------------------------------- owner API
URLS = (("GET", "/api/jeff-kb", None), ("PUT", "/api/jeff-kb/manual", {"name": "a.txt", "data_b64": "YQ=="}),
        ("DELETE", "/api/jeff-kb/manual", None), ("GET", "/api/jeff-kb/sections", None),
        ("PUT", "/api/jeff-kb/mode", {"strict": True}), ("POST", "/api/jeff-kb/check", {"question": "q"}),
        ("GET", "/api/jeff-model", None), ("PUT", "/api/jeff-model", {"model": "m"}),
        ("DELETE", "/api/jeff-model", None))


async def test_api_is_owner_only_and_a_participant_session_is_refused(env, pit_setup):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://test") as anon:
        for method, url, body in URLS:
            assert (await anon.request(method, url, json=body)).status_code == 401, (method, url)
        anon.cookies.set("jeff_session", "participant-session-token")      # Jeff window participant
        for method, url, body in URLS:
            assert (await anon.request(method, url, json=body)).status_code == 401, (method, url)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://test") as browser:
        await browser.post("/api/login", json={"token": env.svc.auth.token})
        assert (await browser.put("/api/jeff-kb/mode", json={"strict": True})).status_code == 403   # no CSRF
    assert not kb.kb_dir(pit_setup).exists()


async def test_api_upload_list_mode_check_and_model(env, pit_setup, monkeypatch):
    from bcc.features import jeff_kb as feature
    prompts = []

    def fake_factory(url, model):
        async def chat(messages):
            prompts.append((url, model, messages))
            has_manual = any(m["content"].startswith("Разделы методички") for m in messages)
            return "Двенадцать минут (§1)." if has_manual else "Обычно минут десять."
        return chat
    monkeypatch.setattr(feature, "chat_factory", fake_factory)
    c = env.client
    data = base64.b64encode(MANUAL_MD.encode("utf-8")).decode()
    r = await c.put("/api/jeff-kb/manual", json={"name": "rules.md", "data_b64": data})
    assert r.status_code == 200 and r.json()["sections"] == 3 and r.json()["name"] == "rules.md"
    assert MARK not in r.text
    assert [s["title"] for s in (await c.get("/api/jeff-kb/sections")).json()["sections"]][0] == "Разминка"
    r = await c.put("/api/jeff-kb/mode", json={"strict": True})
    assert r.json()["strict"] is True and r.json()["scope"] == "owner"
    plain = (await c.post("/api/jeff-kb/check", json={"question": "Сколько длится разминка?",
                                                      "use_manual": False})).json()
    strict = (await c.post("/api/jeff-kb/check", json={"question": "Сколько длится разминка?"})).json()
    assert plain["answer"] == "Обычно минут десять." and plain["cited"] == []
    assert strict["answer"] == "Двенадцать минут (§1)." and strict["cited"] == [{"n": 1, "title": "Разминка"}]
    assert prompts[0][0].startswith("http://127.0.0.1:11434") and prompts[0][1] == "bossman-uncensored-chat:latest"
    nothing = (await c.post("/api/jeff-kb/check", json={"question": "Столица Франции?"})).json()
    assert nothing["answer"] == kb.NO_ANSWER_RU and nothing["no_match"] is True and len(prompts) == 2
    # model choice through the API, then the check uses it
    r = await c.put("/api/jeff-model", json={"model": "gemma4-31b-uncensored:latest"})
    assert r.status_code == 200 and r.json()["effective"] == "gemma4-31b-uncensored:latest"
    await c.post("/api/jeff-kb/check", json={"question": "Сколько длится разминка?"})
    assert prompts[-1][1] == "gemma4-31b-uncensored:latest"
    assert (await c.put("/api/jeff-model", json={"model": "liquid/lfm-7b"})).status_code == 422
    assert (await c.delete("/api/jeff-model")).json()["override"] is None
    bad = await c.put("/api/jeff-kb/manual", json={"name": "a.exe", "data_b64": data})
    assert bad.status_code == 422
    assert (await c.delete("/api/jeff-kb/manual")).json()["present"] is False
    assert MARK not in _all_text_under(pit_home(pit_setup) / "logs")
