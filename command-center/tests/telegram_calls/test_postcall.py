"""Post-call: memory note + inert drafts through the REAL memory service and tasks table (tmp data dirs)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from bcc.db import Database, tasks as tasks_t, task_runs
from bcc.events import EventBus
from bcc.features.tools_memory import save_config
from bcc.secrets import Vault
from bcc.telegram_calls.postcall import MAX_TASKS, PostCall, safe_call_id


def rec(call_id="c1700000-ab12", outcome="completed", tasks=("Отправить отчёт Ивану",), by="qwen", transport="telegram",
        text="Обсудили отчёт."):
    return {"call_id": call_id, "transport": transport, "outcome": outcome, "started_at": 100.0, "ended_at": 160.0,
            "summary": {"text": text, "agreed_tasks": list(tasks), "generated_by": by}}


@pytest.fixture
async def svc(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'bcc.db'}")
    await db.create_all()
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    s = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path), db=db, vault=Vault(tmp_path), bus=EventBus(db),
                        _memory_cache=None, vault_dir=vault_dir)
    yield s
    await db.engine.dispose()


async def configure(svc):
    await save_config(svc, {"root": str(svc.vault_dir)})


async def all_tasks(svc):
    async with svc.db.session() as s:
        return [dict(r._mapping) for r in (await s.execute(sa.select(tasks_t))).fetchall()]


async def test_summary_goes_to_memory_once_with_call_tags(svc):
    await configure(svc)
    pc = PostCall.for_services(svc)
    r1 = await pc.run(rec())
    assert r1["memory"] == "written"
    files = list((svc.vault_dir / "BOSSMAN Memory").glob("*.md"))
    assert [f.name for f in files] == ["call-c1700000-ab12.md"]
    text = files[0].read_text(encoding="utf-8")
    assert "kind: session" in text and "telegram-call" in text and "call-c1700000-ab12" in text
    assert "Обсудили отчёт." in text and "исход: completed" in text and "60 с" in text
    r2 = await pc.run(rec())
    assert r2["memory"] == "exists" and len(list((svc.vault_dir / "BOSSMAN Memory").glob("*.md"))) == 1   # idempotent


async def test_agreed_tasks_become_inert_idempotent_drafts(svc):
    await configure(svc)
    pc = PostCall.for_services(svc)
    r = rec(tasks=("Отправить отчёт", "Позвонить в банк"))
    out = await pc.run(r)
    assert [d["request_id"] for d in out["drafts"]] == ["call-c1700000-ab12-t1", "call-c1700000-ab12-t2"]
    rows = await all_tasks(svc)
    assert len(rows) == 2 and {t["status"] for t in rows} == {"draft"}
    assert {t["meta"]["client_request_id"] for t in rows} == {"call-c1700000-ab12-t1", "call-c1700000-ab12-t2"}
    assert all(t["agent_id"] is None and t["schedule_id"] is None for t in rows)
    assert "ЧЕРНОВИК" in rows[0]["prompt"] and "не выполнять без решения владельца" in rows[0]["prompt"]
    async with svc.db.session() as s:
        assert (await s.execute(sa.select(sa.func.count()).select_from(task_runs))).scalar() == 0   # never enqueued
    again = await pc.run(r)
    assert all(d["replayed"] for d in again["drafts"]) and len(await all_tasks(svc)) == 2


@pytest.mark.parametrize("outcome,by", [("stopped", "qwen"), ("unknown", "qwen"), ("failed", "qwen"), ("connection_lost", "qwen"),
                                        ("completed", "mechanical"), ("completed", "none")])
async def test_no_drafts_after_stop_failure_or_mechanical_summary(svc, outcome, by):
    await configure(svc)
    out = await PostCall.for_services(svc).run(rec(outcome=outcome, by=by))
    assert out["drafts"] == [] and await all_tasks(svc) == []
    assert out["memory"] == "written"                 # the honest short summary is still saved


@pytest.mark.parametrize("outcome", ["completed", "max_duration", "silence_timeout"])
async def test_drafts_after_a_real_ended_conversation(svc, outcome):
    await configure(svc)
    out = await PostCall.for_services(svc).run(rec(outcome=outcome))
    assert len(out["drafts"]) == 1 and len(await all_tasks(svc)) == 1


async def test_loopback_selftest_writes_nothing(svc):
    await configure(svc)
    out = await PostCall.for_services(svc).run(rec(transport="loopback"))
    assert out == {"memory": "skipped_loopback", "drafts": []}
    assert not (svc.vault_dir / "BOSSMAN Memory").exists() and await all_tasks(svc) == []


async def test_memory_not_configured_is_reported_but_drafts_still_created(svc):
    out = await PostCall.for_services(svc).run(rec())
    assert out["memory"] == "error:MemoryNotConfigured" and len(out["drafts"]) == 1


async def test_peer_text_stays_data_limited_and_cannot_flood(tmp_path):
    made = []

    async def draft(**kw):
        made.append(kw)
        return {"id": len(made)}

    async def mem(**kw):
        made.append(("mem", kw))
    tasks = [f"задача {i}\x00\x1b[31m" + "я" * 900 for i in range(MAX_TASKS + 4)]
    out = await PostCall(mem, draft).run(rec(tasks=tasks + ["   "]))
    drafts = [m for m in made if isinstance(m, dict)]
    assert len(drafts) == MAX_TASKS and len(out["drafts"]) == MAX_TASKS
    for d in drafts:
        assert "\x00" not in d["prompt"] and "\x1b" not in d["prompt"] and len(d["prompt"]) < 700
        assert d["request_id"].startswith("call-c1700000-ab12-t")


async def test_hostile_call_id_cannot_escape_the_memory_root(tmp_path):
    seen = []

    async def mem(**kw):
        seen.append(kw)
    await PostCall(mem, None).run(rec(call_id="../../etc/passwd"))
    assert seen[0]["filename"] == "call-....etcpasswd.md" and "/" not in seen[0]["filename"] and "\\" not in seen[0]["filename"]
    assert safe_call_id("../../etc/passwd") == "....etcpasswd"
    assert safe_call_id("").startswith("x") and len(safe_call_id("a")) >= 3
    assert safe_call_id("call-1_A.b") == "call-1_A.b"


async def test_report_never_raises_on_backend_failures(tmp_path):
    async def bad_mem(**kw):
        raise RuntimeError("boom")

    async def bad_draft(**kw):
        raise RuntimeError("boom")
    out = await PostCall(bad_mem, bad_draft).run(rec())
    assert out["memory"] == "error:RuntimeError" and out["drafts"][0]["error"] == "RuntimeError"
