"""Регрессы на три аудитные находки (red-team v1: строки 61, 91, 97):

- (61) Повторная дистилляция текста с уже продвинутой памятью понижала её до
  CANDIDATE и затирала provenance (last_verified_at/supersedes/contradicted_by).
- (91) State.save() писал state.json truncate+write — краш посреди записи
  оставлял обрезанный JSON, проект переставал загружаться.
- (97) Долгий await в раннере + /pause с Пульта: устаревший in-memory снимок
  перезатирал 'paused' при следующем save() и проект продолжал тратить бюджет.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from bossman.context_engine import ContextStore, MemoryKind, MemoryManager, MemoryStatus
from bossman.context_engine.distill import KnowledgeDistiller


def _mgr(tmp_path: Path):
    store = ContextStore(tmp_path / "context.db")
    return store, MemoryManager(store)


def _all(store: ContextStore, project: str = "p"):
    return {m.memory_id: m for m in store.memories(project, tuple(MemoryStatus))}


DECISION_DOC = "Решение: делаем extractive-first сжатие для компакта."


# ---------- (61) повторная дистилляция не понижает статус и не затирает provenance ----------

def test_re_distill_keeps_active_status_and_provenance(tmp_path):
    store, mem = _mgr(tmp_path)
    first = KnowledgeDistiller(mem).extract(DECISION_DOC, project="p", source_refs=["chat:1"]).candidates[0]
    mem.promote(first.memory_id, verified=True)
    promoted = _all(store)[first.memory_id]
    assert promoted.status == MemoryStatus.ACTIVE
    assert promoted.confidence == pytest.approx(.9)
    verified_at = promoted.last_verified_at
    assert verified_at

    second = KnowledgeDistiller(mem).extract(DECISION_DOC, project="p", source_refs=["chat:2"]).candidates[0]
    # возвращённая запись
    assert second.memory_id == first.memory_id
    assert second.status == MemoryStatus.ACTIVE            # не понижена до CANDIDATE
    assert second.last_verified_at == verified_at          # верификация пережила ре-дистилляцию
    assert set(second.source_refs) == {"chat:1", "chat:2"}  # источники объединены
    assert second.confidence == pytest.approx(.9)           # max(0.9, 0.82)
    assert second.importance == pytest.approx(promoted.importance)
    prov = second.metadata["provenance"]
    assert set(prov["source"]) == {"chat:1", "chat:2"}
    # и то, что реально в store
    stored = _all(store)[first.memory_id]
    assert stored.status == MemoryStatus.ACTIVE
    assert stored.last_verified_at == verified_at
    assert set(stored.source_refs) == {"chat:1", "chat:2"}
    # продвинутая память по-прежнему видна retrieve (ACTIVE/DISPUTED)
    assert any(m.memory_id == first.memory_id
               for m in mem.retrieve("extractive сжатие компакта", project="p"))
    store.close()


def test_re_distill_keeps_disputed_status_and_contradiction_link(tmp_path):
    store, mem = _mgr(tmp_path)
    a = mem.constraint("Облачные вызовы разрешены для этого агента.", project="p", source_refs=["s:1"])
    mem.promote(a.memory_id, verified=True)
    b = mem.constraint("Облачные вызовы не разрешены для этого агента.", project="p", source_refs=["s:2"])
    assert b.status == MemoryStatus.DISPUTED
    assert a.memory_id in b.contradicted_by

    b2 = mem.constraint("Облачные вызовы не разрешены для этого агента.", project="p", source_refs=["s:3"])
    assert b2.memory_id == b.memory_id
    assert b2.status == MemoryStatus.DISPUTED               # не понижена, не перезаписана
    assert set(b2.contradicted_by) == {a.memory_id}         # ссылка на противоречие жива
    assert set(b2.source_refs) == {"s:2", "s:3"}
    stored = _all(store)[b.memory_id]
    assert stored.status == MemoryStatus.DISPUTED
    assert set(stored.contradicted_by) == {a.memory_id}
    store.close()


def test_re_distill_keeps_superseded_status_and_supersedes_link(tmp_path):
    store, mem = _mgr(tmp_path)
    d1 = mem.decision("Старое решение: хранить память одним JSON.", project="p", source_refs=["s:1"])
    d2 = mem.decision("Новое решение: раздельные kind-namespaces памяти.", project="p", source_refs=["s:2"])
    mem.promote(d1.memory_id, verified=True)
    mem.promote(d2.memory_id, verified=True)
    mem.supersede(d1.memory_id, d2.memory_id)
    verified2 = _all(store)[d2.memory_id].last_verified_at
    assert verified2

    # повторная запись с тем же явным memory_id (как повторный прогон того же кода)
    r1 = mem.candidate(MemoryKind.DECISION, d1.text, project="p", memory_id=d1.memory_id, source_refs=["s:9"])
    r2 = mem.candidate(MemoryKind.DECISION, d2.text, project="p", memory_id=d2.memory_id, source_refs=["s:9"])
    assert r1.status == MemoryStatus.SUPERSEDED
    assert r2.status == MemoryStatus.ACTIVE
    assert r2.supersedes == [d1.memory_id]                  # ссылка вытеснения не затёрта
    assert r2.last_verified_at == verified2
    assert set(r2.source_refs) == {"s:2", "s:9"}
    rows = _all(store)
    assert rows[d1.memory_id].status == MemoryStatus.SUPERSEDED
    assert rows[d2.memory_id].supersedes == [d1.memory_id]
    store.close()


def test_new_memory_id_still_candidate_and_scores_are_max(tmp_path):
    store, mem = _mgr(tmp_path)
    m = mem.candidate(MemoryKind.FACT, "Окно модели 65536 токенов.", project="p",
                      confidence=.5, importance=.4, source_refs=["a:1"])
    assert m.status == MemoryStatus.CANDIDATE
    mem.promote(m.memory_id, verified=True)

    again = mem.candidate(MemoryKind.FACT, "Окно модели 65536 токенов.", project="p",
                          confidence=.3, importance=.95, source_refs=["b:2"])
    assert again.status == MemoryStatus.ACTIVE
    assert again.confidence == pytest.approx(.9)     # max(0.9, 0.3)
    assert again.importance == pytest.approx(.95)    # max(0.4, 0.95)
    assert set(again.source_refs) == {"a:1", "b:2"}
    assert again.last_verified_at                    # верификация сохранена

    fresh = mem.candidate(MemoryKind.FACT, "Другой факт про кеш промптов.", project="p",
                          confidence=.3, importance=.2)
    assert fresh.status == MemoryStatus.CANDIDATE    # новый memory_id — обычный кандидат
    store.close()


def test_re_candidate_preserves_provenance_metadata(tmp_path):
    store, mem = _mgr(tmp_path)
    m = mem.candidate(MemoryKind.FACT, "Провенанс переживает повторную запись.", project="p",
                      source_refs=["audit:1"], verification="review:alice", confidence=.7)
    mem.promote(m.memory_id, verified=True)
    m2 = mem.candidate(MemoryKind.FACT, "Провенанс переживает повторную запись.", project="p",
                       source_refs=["audit:2"], confidence=.4)
    prov = m2.metadata["provenance"]
    assert prov["verification"] == "review:alice"    # не стёрта пустой пере-записью
    assert set(prov["source"]) == {"audit:1", "audit:2"}
    assert prov["content_hash"]
    store.close()


# ---------- (91) атомарный save: state.json всегда либо старый, либо новый целиком ----------

def _use_projects_dir(tmp_path: Path, monkeypatch, slug: str):
    import bossman.projects.plan as plan_mod
    monkeypatch.setattr(plan_mod.settings, "projects_dir", tmp_path)
    return plan_mod


def test_state_save_atomic_on_crash_mid_write(tmp_path, monkeypatch):
    plan_mod = _use_projects_dir(tmp_path, monkeypatch, "atomic-slug")
    st = plan_mod.State("atomic-slug")
    st.data["status"] = "running"
    st.save()
    original = st.path.read_text(encoding="utf-8")
    assert json.loads(original)["status"] == "running"

    st.data["status"] = "paused"
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        if calls["n"] == 0:                       # «краш» после записи tmp, до replace
            calls["n"] += 1
            raise OSError("simulated crash mid-write")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)
    with pytest.raises(OSError):
        st.save()
    # оригинал цел: не обрезан, не пуст, старое содержимое
    assert st.path.read_text(encoding="utf-8") == original
    # временный файл убран
    assert not list(st.path.parent.glob(".state-*.tmp"))

    st.save()                                     # второй вызов проходит
    final = json.loads(st.path.read_text(encoding="utf-8"))
    assert final["status"] == "paused"            # новый контент целиком
    bak = st.path.with_name("state.json.bak")
    assert json.loads(bak.read_text(encoding="utf-8"))["status"] == "running"


def test_state_save_roundtrip_format_identical(tmp_path, monkeypatch):
    plan_mod = _use_projects_dir(tmp_path, monkeypatch, "fmt-slug")
    st = plan_mod.State("fmt-slug")
    st.data.update({"status": "running", "spent": 1.25, "tasks": {"1": {"status": "done"}}})
    st.save()
    on_disk = json.loads(st.path.read_text(encoding="utf-8"))
    assert on_disk["status"] == "running"
    assert on_disk["spent"] == 1.25
    assert on_disk["tasks"]["1"]["status"] == "done"
    assert "updated_at" in on_disk


def test_state_init_falls_back_to_bak_on_corrupt_file(tmp_path, monkeypatch):
    plan_mod = _use_projects_dir(tmp_path, monkeypatch, "corrupt-slug")
    d = tmp_path / "corrupt-slug"
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text('{"status": "run', encoding="utf-8")  # обрезан крашем
    (d / "state.json.bak").write_text('{"status": "paused", "tasks": {}}', encoding="utf-8")
    st = plan_mod.State("corrupt-slug")
    assert st.data["status"] == "paused"          # проект не «кирпич», читаем .bak


# ---------- (97) пауза во время долгого await не теряется, платные задачи не стартуют ----------

async def _async_none(*a, **k):
    return None


@pytest.mark.asyncio
async def test_pause_during_long_await_stops_runner_before_next_task(tmp_path, monkeypatch):
    import bossman.projects.plan as plan_mod
    import bossman.projects.runner as R
    from bossman.projects.router import Route

    monkeypatch.setattr(plan_mod.settings, "projects_dir", tmp_path)

    async def fake_db_execute(sql, *args):
        return "OK"

    async def fake_db_fetchrow(sql, *args):
        return {"id": 1, "budget_limit": 0}

    monkeypatch.setattr(R.db, "execute", fake_db_execute)
    monkeypatch.setattr(R.db, "fetchrow", fake_db_fetchrow)
    monkeypatch.setattr(R.events, "emit", lambda *a, **k: None)
    monkeypatch.setattr(R.telegram, "notify", _async_none)

    t1 = plan_mod.PlanTask(id="1", name="клип раз", tool="tts", stage="stage1")
    t2 = plan_mod.PlanTask(id="2", name="клип два", tool="tts", stage="stage1")
    monkeypatch.setattr(R, "load_plan", lambda slug: plan_mod.Plan(title="тест", tasks=[t1, t2]))
    monkeypatch.setattr(R, "choose", lambda *a, **k: Route("fake", {"kind": "builtin"}, "fake"))

    executed: list[str] = []
    started = asyncio.Event()
    gate = asyncio.Event()

    async def fake_execute(slug, t, route, state):
        executed.append(t.id)
        if t.id == "1":
            started.set()
            await gate.wait()   # «долгая генерация»: пользователь успевает нажать стоп
        return [f"assets/{t.id}.mp4"], 0.5

    monkeypatch.setattr(R, "_execute", fake_execute)

    async def pause_writer():
        await started.wait()
        st = plan_mod.State("pause-slug")     # ровно то, что делает POST /projects/{slug}/pause
        st.data["status"] = "paused"
        st.save()
        gate.set()

    await asyncio.gather(R._run_project_locked("pause-slug"), pause_writer())

    assert executed == ["1"], "вторая платная задача не должна была стартовать"
    final = plan_mod.State("pause-slug")
    assert final.data["status"] == "paused"   # пауза не перезатёрта 'running'
    assert "2" not in final.data.get("tasks", {})
