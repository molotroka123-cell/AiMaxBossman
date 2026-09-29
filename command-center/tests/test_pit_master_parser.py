"""Jeff Master Parser: collect every conversation, update passports, never delete."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from bcc.pit.config import PITSettings, save_setup
from bcc.pit.master_parser import engine
from bcc.pit.master_parser.engine import Options, resolve_settings, revert_run, run_master_parse
from bcc.pit.models import ConsentState
from bcc.pit.vault import PersonaVault
from bcc.telegram_companion.config import Person
from bcc.telegram_companion.store import Store

SALT = "01" * 32
ALICE, BOB, CAROL = 111, 222, 333


@pytest.fixture(autouse=True)
def _no_private_blocklist(tmp_path, monkeypatch):
    """Tests never read the owner's private Jeff blocklist."""
    monkeypatch.setenv("BOSSMAN_JEFF_BLOCKLIST", str(tmp_path / "no-blocklist.txt"))


def settings_for(root: Path, **kw) -> PITSettings:
    return PITSettings(data_dir=root, people=(Person(ALICE, ALICE, "owner"),),
                       identity_salt=SALT, bot_token="1:test", local_url="http://127.0.0.1:11434/v1",
                       local_models=("x",), **kw)


def build_root(root: Path, salt: str = SALT) -> dict:
    """A fake data root: Telegram Jeff store, web store, vault consent, an export."""
    home = root / "pit-v1.7"
    store = Store(home)
    t0 = time.time() - 3600
    rows = [
        (1, f"{ALICE}:{ALICE}", {"_user_id": ALICE, "_message_id": 10, "text": "Меня зовут Алиса."}, t0),
        (2, f"{ALICE}:{ALICE}", {"_user_id": ALICE, "_message_id": 11,
                                 "text": "Я люблю горные походы и фотографию."}, t0 + 60),
        (3, f"{BOB}:{BOB}", {"_user_id": BOB, "_message_id": 5, "text": "Я работаю над игрой про космос."}, t0 + 90),
        (4, f"{ALICE}:{ALICE}", {"_user_id": ALICE, "_message_id": 12, "text": "/memory"}, t0 + 100),
        (5, f"{CAROL}:{CAROL}", {"_user_id": CAROL, "_message_id": 3, "text": "Я люблю джаз."}, t0 + 110),
    ]
    for update_id, who, body, created in rows:
        store.db.execute("INSERT INTO inbox(id,who,body,lane,phase,created) VALUES(?,?,?,?,?,?)",
                         (update_id, who, store.seal(body), "chat", "done", created))
    # the answered turn duplicates inbox #2 in learning_log (same text) + adds the reply
    store.db.execute("INSERT INTO learning_log(who,body,created) VALUES(?,?,?)",
                     (f"{ALICE}:{ALICE}", store.seal({"user": "Я люблю горные походы и фотографию.",
                                                      "assistant": "Здорово! Где ты ходишь в походы?"}),
                      t0 + 65))
    store.db.execute("INSERT INTO learning_log(who,body,created) VALUES(?,?,?)",
                     (f"{ALICE}:{ALICE}", store.seal({"user": "В основном по Алтаю, летом.",
                                                      "assistant": "Алтай прекрасен."}), t0 + 120))
    store.close()
    web = Store(home / "web")
    web.db.execute("INSERT INTO learning_log(who,body,created) VALUES(?,?,?)",
                   ("7:7", web.seal({"user": "Я предпочитаю короткие ответы.", "assistant": "Понял."}), t0))
    web.close()
    vault = PersonaVault(root, bytes.fromhex(salt))
    keys = {uid: vault.key_for_telegram(uid) for uid in (ALICE, BOB, CAROL)}
    vault.set_consent(keys[ALICE], ConsentState(memory_enabled=True))
    vault.set_consent(keys[BOB], ConsentState(memory_enabled=True))
    # CAROL never agreed to memory
    inbox = home / "master-parser" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "result.json").write_text(json.dumps({
        "name": "Bob", "type": "personal_chat", "id": BOB, "messages": [
            {"id": 1, "type": "message", "date_unixtime": str(int(t0 - 86400)), "from_id": f"user{BOB}",
             "text": ["Я ", {"type": "bold", "text": "учусь"}, " играть на гитаре."]},
            {"id": 2, "type": "message", "date_unixtime": str(int(t0 - 86300)), "from_id": "user999",
             "text": "Круто"},
        ]}, ensure_ascii=False), encoding="utf-8")
    (inbox / "stranger.json").write_text(json.dumps({
        "name": "Stranger", "type": "personal_chat", "id": 555, "messages": [
            {"id": 1, "type": "message", "date_unixtime": str(int(t0)), "from_id": "user555",
             "text": "Я люблю чужие секреты"}]}), encoding="utf-8")
    return {"home": home, "keys": keys, "vault": vault}


class FakeModel:
    """Returns one fact per [P] line mentioning a hobby word; records every prompt."""

    def __init__(self, fail_after: int | None = None):
        self.prompts: list[str] = []
        self.fail_after = fail_after

    async def chat(self, model, messages, **kw):
        if self.fail_after is not None and len(self.prompts) >= self.fail_after:
            raise RuntimeError("local model down")
        user = messages[-1]["content"]
        self.prompts.append(user)
        facts = []
        for line in user.splitlines():
            parts = line.split(" ", 2)
            if len(parts) < 3 or parts[1] != "[P]":
                continue
            if "Алта" in parts[2]:
                facts.append({"category": "travel", "key": "trip_style", "value": "ходит в походы по Алтаю",
                              "evidence": [parts[0]], "confidence": 0.9})
            if "гитар" in parts[2]:
                facts.append({"category": "knowledge", "key": "learning_goals", "value": "учится играть на гитаре",
                              "evidence": [parts[0]]})
            if "космос" in parts[2]:
                facts.append({"category": "work", "key": "active_projects", "value": "делает игру про космос",
                              "evidence": [parts[0]]})
                facts.append({"category": "health", "key": "x", "value": "выдумка", "evidence": [parts[0]]})
        return SimpleNamespace(text=json.dumps({"facts": facts}, ensure_ascii=False), finish="stop")


def run(settings, adapter, **kw):
    options = Options(checkpoint=False, cloud=False, **kw)
    return asyncio.run(run_master_parse(settings, options, adapter=adapter))


def facts_of(vault: PersonaVault, key: str) -> list[dict]:
    return list(vault.iter_candidate_records(key))


def source_files(root: Path) -> list[Path]:
    home = root / "pit-v1.7"
    return [home / "companion.sqlite3", home / "web" / "companion.sqlite3",
            home / "master-parser" / "inbox" / "result.json", home / "secret.key"]


def digest(paths) -> dict:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}


def test_collects_all_sources_and_updates_passports_with_provenance(tmp_path):
    data = build_root(tmp_path)
    before = digest(source_files(tmp_path))
    report = run(settings_for(tmp_path), FakeModel())
    assert digest(source_files(tmp_path)) == before, "sources must never change"
    names = {s["source"]: s for s in report["sources"]}
    assert names["jeff-telegram"]["status"] == "ok" and names["jeff-web"]["status"] == "ok"
    # 5 inbox + 2 log turns (4 msgs, 1 user text duplicated) + web 2 + export 2 (stranger dropped)
    assert report["corpus_messages"] == 5 + 3 + 2 + 2
    alice = data["keys"][ALICE]
    by_value = {f["value"]: f for f in facts_of(data["vault"], alice)}
    assert "ходит в походы по Алтаю" in by_value
    assert any(v.startswith("горные походы") for v in by_value)   # live deterministic extractor
    live_id = next(f for f in by_value.values() if f["value"].startswith("горные походы"))["id"]
    assert live_id == "turn:11:1", "Telegram facts reuse the live Jeff id (message 11)"
    person = next(p for p in report["participants"] if p["person_key"] == alice)
    added = {f["value"]: f for f in person["facts_added"]}
    proof = added["ходит в походы по Алтаю"]["evidence"][0]
    assert proof["snippet"].startswith("В основном по Алтаю") and proof["source"].startswith("jeff-telegram")
    # facts are audited exactly like a live Jeff write
    audit = data["vault"].memory_audit(alice)
    assert audit and all(row["action"] == "write" and row["actor"] == "jeff" for row in audit)
    # the export gave Bob's guitar fact, attributed to Bob only
    bob = data["keys"][BOB]
    assert "учится играть на гитаре" in {f["value"] for f in facts_of(data["vault"], bob)}
    assert report["totals"]["rejected"] >= 1       # invented "health" category refused


def test_second_run_is_idempotent(tmp_path):
    data = build_root(tmp_path)
    model = FakeModel()
    run(settings_for(tmp_path), model)
    first_calls = len(model.prompts)
    snapshot = {k: (data["vault"].person_dir(k) / "facts.jsonl").read_bytes()
                for k in data["keys"].values() if (data["vault"].person_dir(k) / "facts.jsonl").is_file()}
    report = run(settings_for(tmp_path), model)
    assert report["totals"]["collected_new"] == 0
    assert report["totals"]["facts_added"] == 0
    assert len(model.prompts) == first_calls, "analyzed messages are not sent to the model again"
    for key, blob in snapshot.items():
        assert (data["vault"].person_dir(key) / "facts.jsonl").read_bytes() == blob


def test_incremental_only_new_messages(tmp_path):
    data = build_root(tmp_path)
    model = FakeModel()
    run(settings_for(tmp_path), model)
    store = Store(data["home"])
    store.db.execute("INSERT INTO inbox(id,who,body,lane,phase,created) VALUES(?,?,?,?,?,?)",
                     (9, f"{BOB}:{BOB}", store.seal({"_user_id": BOB, "_message_id": 6,
                                                     "text": "Я учусь рисовать."}), "chat", "done", time.time()))
    store.close()
    model.prompts.clear()
    report = run(settings_for(tmp_path), model)
    assert report["totals"]["collected_new"] == 1
    assert len(model.prompts) == 1 and "рисовать" in model.prompts[0]
    assert "космос" not in model.prompts[0]


def test_participants_are_isolated(tmp_path):
    data = build_root(tmp_path)
    model = FakeModel()
    run(settings_for(tmp_path), model)
    for prompt in model.prompts:
        alice = "Алтай" in prompt or "Алиса" in prompt
        bob = "космос" in prompt or "гитар" in prompt
        assert not (alice and bob), "one prompt never mixes two participants"
    alice_values = {f["value"] for f in facts_of(data["vault"], data["keys"][ALICE])}
    assert not any("космос" in v or "гитар" in v for v in alice_values)


def test_no_memory_consent_means_collected_but_never_analyzed(tmp_path):
    data = build_root(tmp_path)
    model = FakeModel()
    report = run(settings_for(tmp_path), model)
    carol = next(p for p in report["participants"] if p["person_key"] == data["keys"][CAROL])
    assert carol["status"] == "NO_MEMORY_CONSENT" and carol["messages_total"] == 1
    assert not facts_of(data["vault"], data["keys"][CAROL])
    assert not any("джаз" in p for p in model.prompts)


def test_dry_run_writes_nothing(tmp_path):
    data = build_root(tmp_path)
    before = digest(source_files(tmp_path))
    report = run(settings_for(tmp_path), FakeModel(), dry_run=True)
    assert report["dry_run"] and report["totals"]["facts_added"] >= 2
    assert digest(source_files(tmp_path)) == before
    parser = data["home"] / "master-parser"
    assert sorted(p.name for p in parser.iterdir()) == ["inbox"], "no corpus, status or report"
    for key in data["keys"].values():
        assert not facts_of(data["vault"], key)


def test_resumes_after_a_failed_batch(tmp_path):
    data = build_root(tmp_path)
    broken = FakeModel(fail_after=1)
    report = run(settings_for(tmp_path), broken, concurrency=1)
    assert sum(p["llm_errors"] for p in report["participants"]) >= 1
    done_before = len(broken.prompts)
    healthy = FakeModel()
    report = run(settings_for(tmp_path), healthy, concurrency=1)
    assert report["totals"]["collected_new"] == 0
    assert healthy.prompts, "failed batches are retried"
    # the batch that succeeded in run 1 is not re-sent
    assert all(p not in healthy.prompts for p in broken.prompts[:done_before])
    values = set()
    for key in data["keys"].values():
        values |= {f["value"] for f in facts_of(data["vault"], key)}
    assert {"ходит в походы по Алтаю", "учится играть на гитаре", "делает игру про космос"} <= values


def test_forgotten_and_deleted_facts_are_not_resurrected(tmp_path):
    data = build_root(tmp_path)
    alice = data["keys"][ALICE]
    vault = data["vault"]
    # the participant once told Jeff to forget anything about «алтаю»
    (vault.ensure(alice) / "corrections.jsonl").write_text(
        json.dumps({"action": "forget", "candidate_id": "x", "query": "Алтаю"}, ensure_ascii=False) + "\n"
        + json.dumps({"action": "delete", "candidate_id": "turn:11:1", "actor": "participant"}) + "\n",
        encoding="utf-8")
    report = run(settings_for(tmp_path), FakeModel())
    values = {f["value"] for f in facts_of(vault, alice)}
    assert "ходит в походы по Алтаю" not in values
    assert not any(v.startswith("горные походы") for v in values)
    assert report["totals"]["blocked_by_participant"] >= 2


def test_conflict_on_single_valued_key_goes_to_owner_review(tmp_path):
    data = build_root(tmp_path)
    alice = data["keys"][ALICE]
    vault = data["vault"]
    (vault.ensure(alice) / "facts.jsonl").write_text(json.dumps({
        "id": "turn:1:0", "category": "communication", "key": "primary_language", "value": "английский",
        "evidence_kind": "explicit"}, ensure_ascii=False) + "\n", encoding="utf-8")

    class LanguageModel(FakeModel):
        async def chat(self, model, messages, **kw):
            label = next(line.split()[0] for line in messages[-1]["content"].splitlines() if "[P]" in line)
            return SimpleNamespace(text=json.dumps({"facts": [{
                "category": "communication", "key": "primary_language", "value": "русский",
                "evidence": [label]}]}, ensure_ascii=False), finish="stop")

    report = run(settings_for(tmp_path), LanguageModel(), participant=str(ALICE))
    person = report["participants"][0]
    assert [p["person_key"] for p in report["participants"]] == [alice]
    assert person["conflicts"] and person["conflicts"][0]["existing"] == "английский"
    values = [f["value"] for f in facts_of(vault, alice)]
    assert "русский" not in values


def test_revert_removes_exactly_the_run_with_owner_audit(tmp_path):
    data = build_root(tmp_path)
    alice = data["keys"][ALICE]
    vault = data["vault"]
    (vault.ensure(alice) / "facts.jsonl").write_text(json.dumps({
        "id": "old", "category": "food", "key": "cuisines", "value": "грузинская"}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    settings = settings_for(tmp_path)
    report = run(settings, FakeModel())
    assert report["totals"]["facts_added"] >= 3
    result = revert_run(settings, report["run_id"])
    assert result["removed"] == report["totals"]["facts_added"]
    assert [f["id"] for f in facts_of(vault, alice)] == ["old"]
    assert any(row["action"] == "delete" and row["actor"] == "owner" for row in vault.memory_audit(alice))
    # corpus is untouched by a revert: collected conversations are never deleted
    db = sqlite3.connect(data["home"] / "master-parser" / "corpus.sqlite3")
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == report["corpus_messages"]


def test_corpus_text_is_sealed_at_rest(tmp_path):
    build_root(tmp_path)
    run(settings_for(tmp_path), FakeModel())
    raw = (tmp_path / "pit-v1.7" / "master-parser" / "corpus.sqlite3").read_bytes()
    assert "Алтаю".encode("utf-8") not in raw and "гитаре".encode("utf-8") not in raw


def test_second_process_cannot_run_concurrently(tmp_path):
    build_root(tmp_path)
    home = engine.parser_home(tmp_path)
    with engine.run_lock(home):
        with pytest.raises(engine.AlreadyRunning):
            run(settings_for(tmp_path), FakeModel())
        assert engine.is_running(tmp_path)
    assert not engine.is_running(tmp_path)


def test_resolve_settings_never_points_a_copy_at_the_live_root(tmp_path, monkeypatch):
    live, copy = tmp_path / "live", tmp_path / "copy"
    config = live / "pit-v1.7" / "config.json"
    config.parent.mkdir(parents=True)
    save_setup(config, people=[Person(ALICE, ALICE, "owner")], chat_models=["m:free"],
               provider_base_url="https://openrouter.ai/api/v1", core_url="http://127.0.0.1:8800",
               web_only=True, local_url="http://127.0.0.1:11434/v1", local_models=["x"])
    import shutil
    shutil.copytree(live, copy)
    assert json.loads((copy / "pit-v1.7" / "config.json").read_text())["data_dir"] == str(live)
    settings = resolve_settings(copy / "pit-v1.7" / "config.json")
    assert Path(settings.data_dir) == copy


def test_cloud_fallback_only_with_consent_and_budget(tmp_path):
    data = build_root(tmp_path)
    alice = data["keys"][ALICE]
    data["vault"].set_consent(alice, ConsentState(memory_enabled=True, remote_processing_enabled=True))

    class Cloud(FakeModel):
        pass

    class Budget:
        def __init__(self):
            self.spent = 0

        def model_blocked(self, model):
            return False

        def blocked(self):
            return ""

        def spend(self):
            self.spent += 1

    cloud, budget = Cloud(), Budget()
    settings = settings_for(tmp_path, provider_key="k", chat_models=("free/one:free",))
    options = Options(checkpoint=False, cloud=True)
    report = asyncio.run(run_master_parse(settings, options, adapter=FakeModel(fail_after=0),
                                          cloud_adapter=cloud, budget=budget))
    assert budget.spent == len(cloud.prompts) >= 1
    assert all("космос" not in p and "гитар" not in p for p in cloud.prompts), \
        "Bob did not allow remote processing: his text never reaches the cloud"
    assert report["totals"]["llm_calls"]["cloud"] == budget.spent


def test_blocked_participant_is_never_analyzed_and_ids_never_stored(tmp_path, monkeypatch):
    data = build_root(tmp_path)
    blocklist = tmp_path / "private" / "blocked.txt"
    blocklist.parent.mkdir()
    blocklist.write_text(f"# synthetic test id\n{BOB}\n", encoding="utf-8")
    monkeypatch.setenv("BOSSMAN_JEFF_BLOCKLIST", str(blocklist))
    model = FakeModel()
    report = run(settings_for(tmp_path), model)
    bob = next(p for p in report["participants"] if p["person_key"] == data["keys"][BOB])
    assert bob["status"] == "BLOCKED" and report["totals"]["blocked_participants"] == 1
    assert not facts_of(data["vault"], data["keys"][BOB])
    assert not any("космос" in p or "гитар" in p for p in model.prompts)
    # no Telegram id in the report, the status file or the corpus
    parser = tmp_path / "pit-v1.7" / "master-parser"
    texts = [json.dumps(report, ensure_ascii=False), (parser / "status.json").read_text(encoding="utf-8")]
    db = sqlite3.connect(parser / "corpus.sqlite3")
    texts.append(json.dumps([list(r) for r in db.execute("SELECT label, source_ref, platform_message_id "
                                                           "FROM messages")]))
    for text in texts:
        for uid in (ALICE, BOB, CAROL):
            assert f"tg:{uid}" not in text and f"user{uid}" not in text and f":{uid}\"" not in text
