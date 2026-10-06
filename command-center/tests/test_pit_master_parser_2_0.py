"""Master Parser 2.0: empty-answer recovery, requeue semantics, narrative, speed report.

Synthetic corpora and fake adapters only: no real participant data, no real model.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from bcc.pit.master_parser.engine import Options, run_master_parse
from bcc.pit.models import ConsentState
from bcc.pit.ollama_native import EmptyAnswer, OllamaNativeChatAdapter
from bcc.pit.passport_checkpoint import build_checkpoint
from bcc.pit.vault import PersonaVault
from bcc.telegram_companion.store import Store
from tests.test_pit_master_parser import (ALICE, BOB, SALT, FakeModel, build_root,
                                          settings_for)


@pytest.fixture(autouse=True)
def _no_private_blocklist(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_JEFF_BLOCKLIST", str(tmp_path / "no-blocklist.txt"))


def run(settings, adapter, **kw):
    kw.setdefault("narrative", False)
    kw.setdefault("checkpoint", False)
    options = Options(cloud=False, **kw)
    return asyncio.run(run_master_parse(settings, options, adapter=adapter))


def build_many(root: Path, uid: int, count: int, start: int = 0) -> str:
    """A synthetic participant with ``count`` messages and memory consent."""
    home = root / "pit-v1.7"
    store = Store(home)
    t0 = time.time() - 7200
    for n in range(count):
        store.db.execute(
            "INSERT INTO inbox(id,who,body,lane,phase,created) VALUES(?,?,?,?,?,?)",
            (start + n + 1, f"{uid}:{uid}", store.seal({"_user_id": uid, "_message_id": start + n + 1,
                                                 "text": f"Сообщение номер {n + 1} про гитару"}),
             "chat", "done", t0 + n))
    store.close()
    vault = PersonaVault(root, bytes.fromhex(SALT))
    key = vault.key_for_telegram(uid)
    vault.set_consent(key, ConsentState(memory_enabled=True))
    return key


class DegradedModel:
    """Ollama runner that answers empty (eval_count=1) until it is unloaded."""

    def __init__(self, healthy_after_unload: bool = True):
        self.degraded = True
        self.unloads: list[str] = []
        self.healthy_after_unload = healthy_after_unload
        self.chats = 0

    async def chat(self, model, messages, **kw):
        self.chats += 1
        if self.degraded:
            return SimpleNamespace(text="", finish="stop", tokens_out=1)
        return SimpleNamespace(text=json.dumps({"facts": []}), finish="stop", tokens_out=5)

    async def unload(self, model):
        self.unloads.append(model)
        if self.healthy_after_unload:
            self.degraded = False


def test_native_adapter_raises_empty_answer_for_eval_count_one():
    def handler(request):
        return httpx.Response(200, json={"model": "m", "message": {"content": ""}, "done": True,
                                         "done_reason": "stop", "eval_count": 1})

    async def go():
        adapter = OllamaNativeChatAdapter("http://127.0.0.1:11434/v1",
                                          transport=httpx.MockTransport(handler))
        return await adapter.chat("m", [{"role": "user", "content": "x"}])

    with pytest.raises(EmptyAnswer) as info:
        asyncio.run(go())
    assert info.value.eval_count == 1 and info.value.kind == "empty"


def test_native_adapter_unload_posts_keep_alive_zero():
    seen = []

    def handler(request):
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"done": True})

    async def go():
        adapter = OllamaNativeChatAdapter("http://127.0.0.1:11434/v1",
                                          transport=httpx.MockTransport(handler))
        await adapter.unload("m")

    asyncio.run(go())
    assert seen == [("/api/generate", {"model": "m", "keep_alive": 0})]


def test_empty_answer_is_recovered_by_one_unload_then_retry(tmp_path):
    build_root(tmp_path)
    model = DegradedModel()
    report = run(settings_for(tmp_path), model, concurrency=1)
    assert model.unloads, "the degraded runner was unloaded"
    assert all(p["status"] == "OK" for p in report["participants"]
               if p["messages_pending"]), report["participants"]
    total = sum(p["llm"]["empty_answers"] for p in report["participants"])
    assert total >= 1
    assert sum(p["llm"]["recoveries"] for p in report["participants"]) >= 1
    assert sum(p["llm_errors"] for p in report["participants"]) == 0


def test_persistent_empty_answer_is_bounded_and_reported_honestly(tmp_path):
    build_root(tmp_path)
    model = DegradedModel(healthy_after_unload=False)
    report = run(settings_for(tmp_path), model, concurrency=1)
    for person in report["participants"]:
        if not person["messages_pending"]:
            continue
        assert person["status"] == "LLM_FAILED"
        assert person["last_error"] == "EmptyAnswer"
        assert person["llm"]["recoveries"] == 1, "one unload per participant"
        assert person["requeued"] == person["messages_pending"]
        assert person["analyzed"] == 0
    assert len(model.unloads) == sum(1 for p in report["participants"] if p["messages_pending"])


def test_failed_batch_is_requeued_not_marked_analysed(tmp_path):
    key = build_many(tmp_path, ALICE, 55)

    class BatchTwoFails(FakeModel):
        async def chat(self, model, messages, **kw):
            if len(self.prompts) == 1:
                self.prompts.append("failed-batch")
                raise RuntimeError("boom")
            return await super().chat(model, messages, **kw)

    report = run(settings_for(tmp_path), BatchTwoFails(), concurrency=1, batch_size=24)
    person = next(p for p in report["participants"] if p["messages_pending"])
    assert person["status"] == "PARTIAL"
    assert person["requeued"] == 24 and person["analyzed"] == 31
    assert person["llm_errors"] == 1
    healthy = FakeModel()
    report = run(settings_for(tmp_path), healthy, concurrency=1, batch_size=24)
    person = next(p for p in report["participants"] if p["messages_pending"])
    assert person["messages_pending"] == 24 and person["analyzed"] == 24
    assert person["status"] == "OK" and person["requeued"] == 0
    assert key


def test_one_participant_crash_does_not_lose_the_others(tmp_path):
    data = build_root(tmp_path)
    from bcc.pit.master_parser import engine

    original = engine.MasterParser._run_batch

    async def crashing(self, corpus, row, view, batch, by_uid, *, remote_ok):
        if row["person_key"] == data["keys"][ALICE]:
            raise RuntimeError("unexpected")
        return await original(self, corpus, row, view, batch, by_uid, remote_ok=remote_ok)

    engine.MasterParser._run_batch = crashing
    try:
        report = run(settings_for(tmp_path), FakeModel(), concurrency=1)
    finally:
        engine.MasterParser._run_batch = original
    by_key = {p["person_key"]: p for p in report["participants"]}
    assert by_key[data["keys"][ALICE]]["status"] == "ERROR"
    assert by_key[data["keys"][BOB]]["analyzed"] > 0


def test_checkpoint_recovers_through_the_same_route(tmp_path):
    home = tmp_path / "pit-v1.7"
    home.mkdir()
    settings = SimpleNamespace(data_dir=tmp_path, identity_salt=SALT, people=())
    vault = PersonaVault(tmp_path, bytes.fromhex(SALT))
    for uid in (111, 222):
        key = vault.key_for_telegram(uid)
        vault.set_consent(key, ConsentState(memory_enabled=True))
        (vault.ensure(key) / "facts.jsonl").write_text(
            json.dumps({"category": "work", "value": f"факт {uid}", "confidence": 0.8},
                       ensure_ascii=False) + "\n", encoding="utf-8")
    import sqlite3
    with sqlite3.connect(home / "companion.sqlite3") as db:
        db.execute("CREATE TABLE inbox (who TEXT)")
        db.executemany("INSERT INTO inbox VALUES (?)", [("111:111",), ("222:222",)])

    class Degraded(DegradedModel):
        async def chat(self, model, messages, **kw):
            result = await super().chat(model, messages, **kw)
            if result.text:
                return SimpleNamespace(text='{"context":"итог","topic_tag":"тема"}',
                                       finish="stop", tokens_out=9)
            return result

    model = Degraded()
    report = asyncio.run(build_checkpoint(settings, adapter=model))
    assert [row["status"] for row in report["participants"]] == ["DRAFT_REVIEW"] * 2
    assert len(model.unloads) == 1, "shared runner is unloaded once, not per participant"
    stuck = DegradedModel(healthy_after_unload=False)
    report = asyncio.run(build_checkpoint(settings, adapter=stuck))
    assert {row["status"] for row in report["participants"]} == {"EMPTY_ANSWER"}
