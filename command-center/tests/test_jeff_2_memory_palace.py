"""Jeff 2.0 memory_palace: BM25 retrieval, forgetting curve, contradictions, provenance, immediate obedience.

Fakes only: a tmp_path vault, an injected embed callable, an injected clock. No model, network or real data.
"""
from __future__ import annotations

import asyncio
import calendar
import json
import time
from types import SimpleNamespace

import pytest

from bcc.pit import participant_admin, passport, passport_commands as pc
from bcc.pit.j2 import J2Pipeline, TurnContext
from bcc.pit.j2 import memory_palace as mp
from bcc.pit.models import ConsentState, EvidenceKind, MemoryCandidate, Sensitivity
from bcc.pit.vault import PersonaVault

SALT = b"m" * 32
NOW = float(calendar.timegm(time.strptime("2026-09-01T12:00:00", "%Y-%m-%dT%H:%M:%S")))


def run(coro):
    return asyncio.run(coro)


def make_vault(tmp_path):
    return PersonaVault(tmp_path, SALT)


def person(vault, seed=1, **consent):
    key = vault.key_for_telegram(seed)
    flags = {"memory_enabled": True, "raw_history_enabled": True, "personalization_enabled": True}
    flags.update(consent)
    vault.set_consent(key, ConsentState(**flags))
    return key


def add_fact(vault, key, cid, value, *, fact_key="hobbies", category="interests", observed="2026-08-30T10:00:00Z",
             kind=EvidenceKind.EXPLICIT, sensitivity=Sensitivity.NORMAL, confidence=0.8, ref="m1"):
    ok = vault.append_candidate(key, MemoryCandidate(
        id=cid, category=category, key=fact_key, value=value, confidence=confidence, evidence_kind=kind,
        sensitivity=sensitivity, source_message_id=ref, observed_at=observed))
    assert ok
    return cid


def add_event(vault, key, text, at="2026-08-31T10:00:00Z"):
    assert vault.append_raw_event(key, {"role": "user", "text": text, "at": at})


def palace(vault, **kw):
    return mp.MemoryPalace(vault, clock=lambda: NOW, **kw)


def ctx(key, text, **kw):
    return TurnContext(person_key=key, who="tg:1", text=text, memory_enabled=True, **kw)


# ---------------------------------------------------------------- BM25 and text normalisation
def test_stemming_lite_matches_russian_inflections():
    assert mp.tokenize("работаю программистом") == mp.tokenize("работа программиста")
    assert mp.tokenize("Ёлка") == mp.tokenize("елка")


def test_stopwords_and_negations_are_dropped_from_tokens():
    assert mp.tokenize("я не люблю это") == mp.tokenize("люблю")
    assert "не" in mp.tokenize("не люблю", keep_negations=True)


def test_bm25_ranks_the_matching_document_first_and_rare_terms_weigh_more():
    docs = [mp.tokenize(t) for t in ("люблю кофе по утрам", "люблю горы и походы", "живу в Казани")]
    bm = mp.BM25(docs)
    scores = bm.scores(mp.tokenize("кофе"))
    assert scores[0] > 0 and scores[1] == 0 and scores[2] == 0
    assert bm.idf(mp.tokenize("кофе")[0]) > bm.idf(mp.tokenize("люблю")[0])


def test_bm25_length_normalisation_prefers_the_shorter_document():
    bm = mp.BM25([mp.tokenize("кофе"), mp.tokenize("кофе " + "разное слово " * 30)])
    s = bm.scores(mp.tokenize("кофе"))
    assert s[0] > s[1] > 0


def test_bm25_handles_an_empty_corpus_and_empty_query():
    assert mp.BM25([]).scores(["x"]) == []
    assert mp.BM25([["a"]]).scores([]) == [0.0]


def test_cosine_is_safe_for_zero_and_mismatched_vectors():
    assert mp.cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert mp.cosine([0, 0], [1, 1]) == 0.0
    assert mp.cosine([1], [1, 2]) == 0.0


# ---------------------------------------------------------------- retrieval and scoring
def test_retrieves_the_relevant_fact_and_nothing_for_unrelated_query(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "f1", "люблю кофе")
    add_fact(vault, key, "f2", "хожу в горы", fact_key="sport")
    engine = palace(vault)
    hits = run(engine.retrieve(key, "какой кофе мне взять"))
    assert [h.item.id for h in hits] == ["f1"]
    assert run(engine.retrieve(key, "квантовая хромодинамика")) == []


def test_retrieval_is_bounded_by_top_k(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    for n in range(20):
        add_fact(vault, key, f"f{n}", f"люблю кофе номер {n}")
    engine = palace(vault)
    assert len(run(engine.retrieve(key, "кофе", k=3))) == 3
    assert len(run(engine.retrieve(key, "кофе", k=500))) <= mp.MAX_LISTED


def test_forgetting_curve_decays_with_age_and_confirmed_facts_fade_slower(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "old", "люблю чай", observed="2026-02-01T10:00:00Z")
    add_fact(vault, key, "old_c", "люблю чай", observed="2026-02-01T10:00:00Z", kind=EvidenceKind.CONFIRMED)
    add_fact(vault, key, "new", "люблю чай", observed="2026-08-31T10:00:00Z")
    by_id = {i.id: i for i in palace(vault).items(key)}
    engine = palace(vault)
    assert engine.retention(by_id["new"]) > engine.retention(by_id["old"])
    assert engine.retention(by_id["old_c"]) > engine.retention(by_id["old"])
    assert 0 < engine.retention(by_id["old"]) < 1


def test_recent_memory_outranks_an_equally_relevant_stale_one(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "stale", "люблю чай", fact_key="a", observed="2025-01-01T10:00:00Z")
    add_fact(vault, key, "fresh", "люблю чай", fact_key="b", observed="2026-08-31T10:00:00Z")
    assert run(palace(vault).retrieve(key, "чай"))[0].item.id == "fresh"


def test_importance_boosts_confirmed_identity_facts(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "minor", "зовут кот Барсик", fact_key="pet", category="misc", confidence=0.3)
    add_fact(vault, key, "major", "зовут Артём", fact_key="name", category="identity", confidence=0.95,
             kind=EvidenceKind.CONFIRMED)
    items = {i.id: i for i in palace(vault).items(key)}
    assert items["major"].importance > items["minor"].importance
    assert run(palace(vault).retrieve(key, "зовут"))[0].item.id == "major"


def test_events_layer_is_used_only_with_raw_history_consent(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_event(vault, key, "в субботу еду на рыбалку на озеро")
    assert run(palace(vault).retrieve(key, "рыбалка"))[0].item.layer == "events"
    state = vault.consent(key)
    state.raw_history_enabled = False
    vault.set_consent(key, state)
    assert run(palace(vault).retrieve(key, "рыбалка")) == []


def test_style_layer_is_retrieved_and_labelled_as_a_guess(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    assert passport.write_style(vault.person_dir(key), {"status": "OK", "paragraphs": ["Пишет коротко и с юмором."]})
    notes = run(palace(vault).notes_for(key, "как он пишет коротко"))
    assert notes and "предположение" in notes[0]


def test_sensitive_facts_stay_out_of_prompt_notes_without_sensitive_consent(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault, sensitive_memory_enabled=True)
    add_fact(vault, key, "s1", "лечу гастрит", fact_key="health", category="health",
             sensitivity=Sensitivity.SENSITIVE)
    state = vault.consent(key)
    state.sensitive_memory_enabled = False
    vault.set_consent(key, state)
    assert run(palace(vault).notes_for(key, "гастрит")) == ()
    # but the participant can always see it in their own explicit view, labelled local-only
    view = run(palace(vault).what_do_i_know(key, "что ты знаешь про гастрит"))
    assert "гастрит" in view and "только локально" in view


def test_expired_facts_are_not_retrieved(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "e1", "сейчас в отпуске", fact_key="short_term", observed="2026-01-01T00:00:00Z")
    rows = [json.loads(line) for line in (vault.person_dir(key) / "facts.jsonl").read_text("utf-8").splitlines()]
    rows[0]["ttl_seconds"] = 60
    vault._rewrite_facts(key, rows)
    assert run(palace(vault).retrieve(key, "отпуск")) == []


# ---------------------------------------------------------------- embeddings
def fake_embed(batch):
    vocab = ["кофе", "чай", "горы", "напиток"]
    out = []
    for text in batch:
        t = text.lower()
        vec = [1.0 if w in t else 0.0 for w in vocab]
        if "напиток" in t or "кофе" in t or "чай" in t:
            vec[3] = 1.0
        out.append(vec)
    return out


def test_embeddings_find_a_semantic_match_that_lexical_search_misses(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "f1", "люблю чай")
    add_fact(vault, key, "f2", "хожу в горы", fact_key="sport")
    assert run(palace(vault).retrieve(key, "любимый напиток")) == []
    engine = palace(vault, embed=fake_embed)
    hits = run(engine.retrieve(key, "любимый напиток"))
    assert hits and hits[0].item.id == "f1" and engine.counters["embed_used"] == 1


def test_async_embed_callable_is_supported(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "f1", "люблю чай")

    async def aembed(batch):
        return fake_embed(batch)

    assert run(palace(vault, embed=aembed).retrieve(key, "напиток"))[0].item.id == "f1"


def test_a_broken_or_slow_embedder_falls_back_to_lexical(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "f1", "люблю кофе")

    def boom(batch):
        raise RuntimeError("no model")

    async def slow(batch):
        await asyncio.sleep(5)

    for bad in (boom, slow, lambda b: [[1.0]]):
        engine = palace(vault, embed=bad)
        assert run(engine.retrieve(key, "кофе"))[0].item.id == "f1"
        assert engine.counters["embed_failed"] == 1


def test_embedding_cache_is_reused_and_purged_when_a_fact_is_forgotten(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "f1", "люблю чай")
    calls = []

    def counting(batch):
        calls.append(list(batch))
        return fake_embed(batch)

    engine = palace(vault, embed=counting)
    run(engine.retrieve(key, "чай"))
    run(engine.retrieve(key, "чай"))
    assert len(calls[1]) == 1            # only the query is embedded the second time
    assert vault.delete_fact(key, "f1", actor="participant")
    run(engine.retrieve(key, "чай"))
    assert all("чай" not in text for text in engine._vectors[key])


# ---------------------------------------------------------------- contradictions
def test_polarity_contradiction_is_detected(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "a", "люблю кофе", fact_key="likes")
    add_fact(vault, key, "b", "не люблю кофе", fact_key="dislikes")
    engine = palace(vault)
    clashes = engine.contradictions(run(engine.retrieve(key, "кофе")))
    assert len(clashes) == 1 and clashes[0].reason == "утверждение и его отрицание"


def test_single_valued_key_with_two_values_is_a_contradiction(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "a", "Казань", fact_key="city", category="identity")
    add_fact(vault, key, "b", "Самара", fact_key="city", category="identity")
    clashes = mp.detect_contradictions(palace(vault).items(key))
    assert len(clashes) == 1 and "разные значения" in clashes[0].reason


def test_multi_valued_hobbies_are_not_contradictions(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "a", "люблю кофе")
    add_fact(vault, key, "b", "люблю горы")
    assert mp.detect_contradictions(palace(vault).items(key)) == []


def test_contradiction_note_tells_the_model_to_ask_not_to_pick(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "a", "люблю кофе", fact_key="likes")
    add_fact(vault, key, "b", "не люблю кофе", fact_key="dislikes")
    notes = run(palace(vault).notes_for(key, "кофе"))
    assert any("противоречива" in n and "не выбирай сам" in n for n in notes)


# ---------------------------------------------------------------- "what do you know" / "why"
def test_what_do_you_know_lists_facts_with_provenance_and_commands(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "f1", "люблю кофе", ref="voice:9")
    add_fact(vault, key, "f2", "работаю в порту", fact_key="active_projects", ref="command:1")
    answer = run(palace(vault).what_do_i_know(key, "Что ты обо мне знаешь?"))
    assert "люблю кофе" in answer and "голосовое" in answer and "твоя команда" in answer
    assert "уверенность 0.80" in answer and "/forget" in answer and "/correct" in answer


def test_what_do_you_know_with_a_topic_filters(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "f1", "люблю кофе")
    add_fact(vault, key, "f2", "хожу в горы", fact_key="sport")
    answer = run(palace(vault).what_do_i_know(key, "что ты знаешь про кофе"))
    assert "кофе" in answer and "горы" not in answer


def test_what_do_you_know_on_empty_passport_is_honest(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    assert "пуст" in run(palace(vault).what_do_i_know(key, "что ты обо мне знаешь"))


def test_why_answer_points_to_the_stored_evidence(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "f1", "люблю кофе", ref="m5", observed="2026-08-20T10:00:00Z")
    answer = run(palace(vault).why_do_i_think(key, "почему ты думаешь что я люблю кофе?"))
    assert "люблю кофе" in answer and "2026-08-20" in answer and "твоё сообщение" in answer


def test_why_without_a_topic_uses_what_was_just_shown(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "f1", "люблю кофе")
    engine = palace(vault)
    run(engine.notes_for(key, "кофе"))
    assert "люблю кофе" in run(engine.why_do_i_think(key, "откуда ты это знаешь?"))


def test_why_admits_when_nothing_supports_the_claim(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    answer = run(palace(vault).why_do_i_think(key, "почему ты думаешь что я люблю джаз"))
    assert "неподтвержд" in answer


def test_why_marks_style_inference_as_a_guess(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    passport.write_style(vault.person_dir(key), {"status": "OK", "paragraphs": ["Любит шутить иронично."]})
    answer = run(palace(vault).why_do_i_think(key, "почему ты думаешь что я шучу иронично"))
    assert "предположение" in answer


def test_explicit_view_writes_an_audit_row_without_values(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "f1", "люблю кофе")
    run(palace(vault).what_do_i_know(key, "что ты знаешь"))
    rows = vault.memory_audit(key)
    assert rows[-1]["action"] == "view" and rows[-1]["actor"] == "participant"
    assert "кофе" not in json.dumps(rows, ensure_ascii=False)


# ---------------------------------------------------------------- obedience: correct / forget / pause / revoke
def test_correct_is_obeyed_on_the_very_next_turn(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "f1", "живу в Казани", fact_key="city", category="identity")
    engine = palace(vault)
    assert "Казани" in run(engine.notes_for(key, "где я живу город"))[0]
    assert vault.correct_fact(key, "f1", "живу в Самаре", actor="participant")
    notes = run(engine.notes_for(key, "где я живу город"))
    assert "Самаре" in notes[0] and all("Казани" not in n for n in notes)
    assert "Казани" not in run(engine.what_do_i_know(key, "что ты знаешь"))


def test_forget_is_obeyed_immediately_in_retrieval_and_views(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "f1", "люблю кофе")
    engine = palace(vault)
    assert run(engine.retrieve(key, "кофе"))
    assert vault.delete_fact(key, "f1", actor="participant")
    assert run(engine.retrieve(key, "кофе")) == []
    assert "пуст" in run(engine.what_do_i_know(key, "что ты знаешь"))
    assert "неподтвержд" in run(engine.why_do_i_think(key, "почему ты думаешь что я люблю кофе"))


def test_pause_memory_stops_retrieval_and_views_at_once(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "f1", "люблю кофе")
    engine = palace(vault)
    assert run(engine.notes_for(key, "кофе"))
    assert participant_admin.pause_memory(vault, key)
    assert run(engine.notes_for(key, "кофе")) == ()
    assert run(engine.retrieve(key, "кофе")) == []
    assert "Память выключена" in run(engine.what_do_i_know(key, "что ты знаешь"))
    assert engine.items(key) == []


def test_revoke_consent_stops_everything(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "f1", "люблю кофе")
    add_event(vault, key, "люблю кофе по утрам")
    engine = palace(vault)
    assert run(engine.notes_for(key, "кофе"))
    ok, _ = pc.revoke_consent(vault, key)
    assert ok and run(engine.notes_for(key, "кофе")) == ()
    assert "Память выключена" in run(engine.why_do_i_think(key, "почему ты думаешь что я люблю кофе"))


def test_personalization_off_keeps_the_view_but_silences_prompt_notes(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "f1", "люблю кофе")
    pc.set_personalization(vault, key, False)
    engine = palace(vault)
    assert run(engine.notes_for(key, "кофе")) == ()
    view = run(engine.what_do_i_know(key, "что ты знаешь"))
    assert "люблю кофе" in view and "Персонализация выключена" in view


def test_remote_route_without_remote_personalization_gets_no_memory(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "f1", "люблю кофе")
    engine = palace(vault)
    assert run(engine.notes_for(key, "кофе", remote_route=True)) == ()
    state = vault.consent(key)
    state.remote_personalization_enabled = True
    vault.set_consent(key, state)
    assert run(engine.notes_for(key, "кофе", remote_route=True))


# ---------------------------------------------------------------- isolation
def test_two_participants_never_see_each_others_memory(tmp_path):
    vault = make_vault(tmp_path)
    alice, bob = person(vault, 1), person(vault, 2)
    add_fact(vault, alice, "a1", "люблю кофе с корицей")
    add_fact(vault, bob, "b1", "люблю чай с мятой")
    add_event(vault, alice, "секретный план Алисы про кофе")
    engine = palace(vault, embed=fake_embed)
    for query in ("кофе", "чай", "корица мята", "что ты знаешь"):
        blob = json.dumps([run(engine.notes_for(bob, query)), run(engine.what_do_i_know(bob, query)),
                           run(engine.why_do_i_think(bob, "почему ты думаешь " + query))], ensure_ascii=False)
        assert "корицей" not in blob and "Алисы" not in blob
    assert "мятой" not in json.dumps(run(engine.notes_for(alice, "чай мята")), ensure_ascii=False)
    assert {i.id for i in engine.items(bob)} == {"b1"}


def test_index_cache_is_per_person_and_last_shown_is_not_shared(tmp_path):
    vault = make_vault(tmp_path)
    alice, bob = person(vault, 1), person(vault, 2)
    add_fact(vault, alice, "a1", "люблю кофе")
    engine = palace(vault)
    run(engine.notes_for(alice, "кофе"))
    assert "неподтвержд" in run(engine.why_do_i_think(bob, "откуда ты это знаешь"))


# ---------------------------------------------------------------- module through the pipeline
def module(vault, **kw):
    return mp.create(SimpleNamespace(vault=vault), **kw)


def test_create_returns_a_module_with_the_specified_order_and_name(tmp_path):
    m = module(make_vault(tmp_path))
    assert (m.name, m.order) == ("memory_palace", 40)
    assert m.status()["embeddings"] is False and m.status()["counters"]["retrievals"] == 0


def test_pre_route_answers_memory_questions_and_ignores_everything_else(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "f1", "люблю кофе")
    m = module(vault)
    assert "люблю кофе" in run(m.pre_route(ctx(key, "Что ты помнишь обо мне?"))).reply
    assert run(m.pre_route(ctx(key, "какая сегодня погода"))) is None
    assert run(m.pre_route(ctx(key, "/memory что ты знаешь"))) is None
    assert run(m.pre_route(ctx(key, "что ты знаешь " * 60))) is None


def test_general_knowledge_questions_are_not_hijacked(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "f1", "люблю кофе")
    m = module(vault)
    for text in ("What do you know?", "что ты знаешь про Python", "почему ты думаешь что небо голубое",
                 "how do you know that", "откуда ты знаешь про Москву"):
        assert run(m.pre_route(ctx(key, text))) is None, text
    assert "люблю кофе" in run(m.pre_route(ctx(key, "what do you know about me"))).reply
    assert "люблю кофе" in run(m.pre_route(ctx(key, "что ты помнишь?"))).reply


def test_a_bare_why_refers_to_what_was_just_shown(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "f1", "люблю кофе")
    m = module(vault)
    assert run(m.pre_route(ctx(key, "откуда ты это знаешь?"))) is None
    run(m.augment(ctx(key, "посоветуй кофе")))
    assert "люблю кофе" in run(m.pre_route(ctx(key, "откуда ты это знаешь?"))).reply


def test_pipeline_end_to_end_adds_notes_before_the_user_message(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "f1", "люблю кофе")
    pipeline = J2Pipeline([module(vault)])
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "посоветуй кофе"}]
    out = run(pipeline.augment(ctx(key, "посоветуй кофе"), messages))
    assert "люблю кофе" in out[-2]["content"] and out[-1] == messages[-1]
    assert run(pipeline.pre_route(ctx(key, "почему ты думаешь что я люблю кофе"))).startswith("Я помню")


def test_notes_are_bounded_and_secrets_are_redacted(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    for n in range(30):
        add_fact(vault, key, f"f{n}", "люблю кофе " + "очень сильно " * 15 + str(n))
    notes = run(palace(vault).notes_for(key, "кофе"))
    assert sum(len(n) for n in notes) <= mp.NOTE_CHAR_BUDGET + 200 and len(notes) <= mp.TOP_K + 3
    text = "\n".join(notes)
    assert "sk-" not in text


def test_index_cache_reuses_until_the_file_changes(tmp_path):
    vault = make_vault(tmp_path)
    key = person(vault)
    add_fact(vault, key, "f1", "люблю кофе")
    engine = palace(vault)
    first = engine._load(key, ("facts", "style", "events"))
    assert engine._load(key, ("facts", "style", "events")) is first
    add_fact(vault, key, "f2", "люблю чай")
    assert engine._load(key, ("facts", "style", "events")) is not first
