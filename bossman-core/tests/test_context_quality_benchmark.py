"""ЭТАП 2.222 — Context Quality Benchmark (shadow: baseline vs Context Engine).

Для каждой golden-задачи сравнивается baseline (полный сырой контекст) с
оптимизированным контекстом Context Engine. Приёмка: НИ ОДИН обязательный якорь
не потерян и нет регресса детерминированных проверок. Экономия токенов —
вторична; экономия токенов при ухудшении качества = FAIL (гейт качества стоит
раньше и жёстче любого выигрыша по токенам).
"""
import json
import pathlib

import pytest

from bossman.context_engine import ContextEngine, MemoryKind, MemoryStatus, Message

GOLDEN = pathlib.Path(__file__).parent / "fixtures" / "context_golden" / "golden.json"
_TASKS = json.loads(GOLDEN.read_text(encoding="utf-8"))


def _tok(s: str) -> int:
    return max(1, (len(s) + 2) // 3)


def _quality(text: str, anchors: list[str]) -> float:
    if not anchors:
        return 1.0
    return sum(1 for a in anchors if a in text) / len(anchors)


def _build(eng: ContextEngine, task: dict) -> None:
    for d in task.get("docs", []):
        eng.index_text(d["text"], source_uri=d["uri"], source_type=d.get("type", "markdown"),
                       project=task["project"], sensitivity=d.get("sensitivity", "normal"))
    for spec in task.get("memories", []):
        rec = eng.memory.candidate(MemoryKind(spec["kind"]), spec["text"], project=task["project"],
                                   source_refs=spec.get("source_refs", []), memory_id=spec.get("id"))
        if spec.get("status", "candidate") == "active":
            eng.memory.promote(rec.memory_id)
    for spec in task.get("memories", []):
        if spec.get("supersedes"):
            eng.memory.supersede(spec["supersedes"], spec["id"])


def _baseline_text(eng: ContextEngine, task: dict) -> str:
    if task["type"] == "compact":
        return "\n".join(m["content"] for m in task["conversation"])
    parts = [d["text"] for d in task.get("docs", [])]
    # Полный контекст включает и ID записи (как в реальном memory-блоке), чтобы
    # ID-якоря (DEC-0002) присутствовали в baseline для честного сравнения.
    parts += [f"[{m.memory_id}] {m.text}" for m in eng.store.memories(task["project"],
              (MemoryStatus.ACTIVE, MemoryStatus.DISPUTED))]
    return "\n".join(parts)


def _optimized_text(eng: ContextEngine, task: dict) -> str:
    typ = task["type"]
    if typ == "compact":
        msgs = [Message(m["role"], m["content"]) for m in task["conversation"]]
        return eng.compact(msgs, project=task["project"], target_tokens=task["target_tokens"],
                           keep_recent=task["keep_recent"], query=task["query"]).text
    if typ == "sensitive":
        allow = tuple(task["sensitivity_allow"])
        hits = eng.retriever.search(task["query"], project=task["project"], sensitivity_allow=allow)
        return "\n".join(f"### {h.chunk.source_uri}\n{h.chunk.text}" for h in hits)
    # retrieval: реальный adaptive ContextCompiler
    compiled = eng.compiler.compile(model="local", query=task["query"], project=task["project"],
                                    model_window=task.get("model_window", 32768), desired_output=1024)
    return compiled.render()


@pytest.mark.parametrize("task", _TASKS, ids=[t["id"] for t in _TASKS])
def test_no_silent_degradation(task, tmp_path):
    eng = ContextEngine(tmp_path / "context.db")
    _build(eng, task)
    anchors = task["mandatory_anchors"]
    baseline = _baseline_text(eng, task)
    optimized = _optimized_text(eng, task)

    q_base = _quality(baseline, anchors)
    q_opt = _quality(optimized, anchors)

    # sanity: baseline действительно содержит якоря (иначе фикстура сломана)
    assert q_base == 1.0, f"{task['id']}: baseline не содержит якоря — фикстура битая"
    # ГЛАВНЫЙ гейт: ни один обязательный якорь не потерян оптимизацией
    missing = [a for a in anchors if a not in optimized]
    assert not missing, f"{task['id']} ({task['category']}): потеряны якоря {missing}"
    # нет регресса относительно baseline
    assert q_opt >= q_base, f"{task['id']}: регресс качества {q_opt} < {q_base}"

    for src in task.get("expected_sources", []):
        assert src in optimized, f"{task['id']}: нет ожидаемого source {src}"
    for src in task.get("forbidden_sources", []):
        assert src not in optimized, f"{task['id']}: sensitive source {src} просочился"
    for txt in task.get("forbidden_text", []):
        assert txt not in optimized, f"{task['id']}: superseded/скрытый текст {txt} всплыл"
    eng.close()


def test_benchmark_token_report(tmp_path, capsys):
    """Агрегатная экономия токенов — вторичная метрика. Гейт качества уже
    проверен по задачам; здесь фиксируем, что при сохранённом качестве экономия
    не куплена ценой ухудшения (q_opt==1.0 для всех)."""
    total_base = total_opt = 0
    worse = []
    for i, task in enumerate(_TASKS):
        eng = ContextEngine(tmp_path / f"b{i}.db")
        _build(eng, task)
        base = _baseline_text(eng, task)
        opt = _optimized_text(eng, task)
        total_base += _tok(base); total_opt += _tok(opt)
        if _quality(opt, task["mandatory_anchors"]) < 1.0:
            worse.append(task["id"])
        eng.close()
    saved = 100 * (1 - total_opt / max(1, total_base))
    print(f"\n[benchmark] baseline_tokens={total_base} optimized_tokens={total_opt} "
          f"saved={saved:.1f}% quality_regressions={worse}")
    # Экономия токенов при ухудшении качества = FAIL.
    assert not worse, f"качество ухудшилось на задачах {worse} — экономия токенов недопустима"


def test_real_savings_on_large_corpus(tmp_path):
    """Ценность движка виден на большом корпусе: из 40 дистракторов + 1 релевантного
    оптимизированный контекст тащит только релевантное — реальная экономия ПРИ
    сохранённом качестве (якоря на месте). Это win по токенам без ухудшения."""
    eng = ContextEngine(tmp_path / "big.db")
    corpus = []
    for i in range(40):
        txt = f"Заметка {i}: кулинарный рецепт номер {i} про суп, специи и выпечку."
        corpus.append(txt)
        eng.index_text(txt, source_uri=f"noise/{i}.md", source_type="markdown", project="p")
    key = "Ключевой факт: сервер имеет 128 GB RAM и окно модели 65536 токенов."
    corpus.append(key)
    eng.index_text(key, source_uri="key.md", source_type="markdown", project="p")

    anchors = ["128 GB", "65536"]
    baseline = "\n".join(corpus)
    optimized = eng.compiler.compile(model="local", query="сколько RAM и окно сервер",
                                     project="p", model_window=32768, desired_output=1024).render()
    # качество сохранено
    assert all(a in optimized for a in anchors), "якоря потеряны на большом корпусе"
    assert "key.md" in optimized
    # реальная экономия токенов
    assert _tok(optimized) < _tok(baseline)
    # и не тянет все 40 шумовых источников
    assert optimized.count("noise/") < 20
    eng.close()
