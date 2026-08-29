"""ЭТАП 2.222 — слой context_engine поверх реального bossman.context.ContextBuilder.

Проверяем именно точку интеграции: движок наполняет блок retrieved настоящего
ContextBuilder долговременной памятью (с provenance) и evidence-чанками (с
source-refs), а также tool schema pruning. ContextBuilder не заменяется.
"""
from bossman.context import ContextBudget, ContextBuilder
from bossman.context_engine import ContextEngine, MemoryKind, get_engine, prune_tool_schemas


def _engine(tmp_path):
    return ContextEngine(tmp_path / "context.db")


def test_injection_feeds_real_context_builder(tmp_path):
    eng = _engine(tmp_path)
    eng.index_text("# Retrieval\nBOSSMAN использует hybrid lexical и vector retrieval с reranking.",
                   source_uri="docs/retrieval.md", source_type="markdown", project="coder")
    m = eng.memory.candidate(MemoryKind.DECISION, "Использовать reranking после hybrid retrieval.",
                             project="coder", source_refs=["docs/retrieval.md"])
    eng.memory.promote(m.memory_id, verified=True)

    builder = ContextBuilder(ContextBudget(window=64_000), system="Ты coder-агент.")
    injected = eng.inject_into_builder(builder, "как устроен retrieval?", project="coder")
    assert injected, "инъекция пуста"
    # блок retrieved реального билдера наполнен
    assert builder.retrieved
    joined = "\n".join(builder.retrieved)
    # provenance памяти прокинут
    assert m.memory_id in joined
    # evidence с source-ref
    assert "docs/retrieval.md" in joined
    assert "source=" in joined
    # и это попадает в собранный prompt
    prompt = "\n".join(x["content"] for x in builder.build("как устроен retrieval?"))
    assert m.memory_id in prompt
    eng.close()


def test_memory_first_before_evidence(tmp_path):
    eng = _engine(tmp_path)
    eng.index_text("hybrid retrieval reranking evidence chunk", source_uri="a.md",
                   source_type="markdown", project="p")
    m = eng.memory.candidate(MemoryKind.CONSTRAINT, "hybrid retrieval обязателен.",
                             project="p", source_refs=["a.md"])
    eng.memory.promote(m.memory_id, verified=True)
    blocks = eng.build_injection("hybrid retrieval", project="p")
    assert blocks[0].startswith("## Долговременная память")
    eng.close()


def test_engine_degrades_without_crash_on_empty_store(tmp_path):
    eng = _engine(tmp_path)
    builder = ContextBuilder(ContextBudget(window=32_000), system="s")
    injected = eng.inject_into_builder(builder, "ничего не проиндексировано", project="p")
    assert injected == []
    assert builder.retrieved == []
    eng.close()


def test_disputed_memory_labelled(tmp_path):
    eng = _engine(tmp_path)
    a = eng.memory.constraint("Облако разрешено.", project="p", source_refs=["s1"])
    eng.memory.promote(a.memory_id, verified=True)
    b = eng.memory.constraint("Облако не разрешено.", project="p", source_refs=["s2"])
    eng.memory.promote(b.memory_id)
    block = eng.memory_block("Облако разрешено", project="p")
    assert "[DISPUTED]" in block
    eng.close()


def test_get_engine_singleton_per_path(tmp_path):
    e1 = get_engine(tmp_path / "context.db")
    e2 = get_engine(tmp_path / "context.db")
    assert e1 is e2
    e1.close()


def test_runner_apply_context_engine_populates_and_prunes(tmp_path, monkeypatch):
    from bossman import runner
    from bossman.config import settings
    from bossman.context import ContextBudget, ContextBuilder
    from bossman.context_engine import close_all, get_engine

    close_all()
    db = tmp_path / "context.db"
    monkeypatch.setattr(settings, "context_db", db)
    monkeypatch.setattr(settings, "context_engine_enabled", True)
    eng = get_engine(db)
    eng.index_text("hybrid retrieval reranking evidence", source_uri="d.md",
                   source_type="markdown", project="coder")
    m = eng.memory.candidate(MemoryKind.DECISION, "reranking после retrieval",
                             project="coder", source_refs=["d.md"])
    eng.memory.promote(m.memory_id, verified=True)

    builder = ContextBuilder(ContextBudget(window=64_000), system="s")
    tools = [{"type": "function", "function": {"name": f"tool_{i}", "description": "нечто",
              "parameters": {"type": "object", "properties": {}}}} for i in range(20)]
    tools[0]["function"]["name"] = "browser_confirmed_click"
    out = runner.apply_context_engine(builder, tools, project="coder",
                                      task_text="как работает retrieval reranking", memory_md="")
    assert builder.retrieved, "блок retrieved реального билдера не наполнен"
    assert m.memory_id in "\n".join(builder.retrieved)
    assert len(out) < len(tools), "tool pruning не сработал"
    names = [s["function"]["name"] for s in out]
    assert "browser_confirmed_click" in names  # always-safety сохранён
    close_all()


def test_runner_apply_context_engine_disabled_is_noop(tmp_path, monkeypatch):
    from bossman import runner
    from bossman.config import settings
    from bossman.context import ContextBudget, ContextBuilder
    from bossman.context_engine import close_all

    close_all()
    monkeypatch.setattr(settings, "context_engine_enabled", False)
    builder = ContextBuilder(ContextBudget(window=32_000), system="s")
    tools = [{"type": "function", "function": {"name": f"t{i}", "description": "d",
              "parameters": {"type": "object", "properties": {}}}} for i in range(20)]
    out = runner.apply_context_engine(builder, tools, project="p", task_text="задача", memory_md="")
    assert out is tools
    assert builder.retrieved == []


def test_tool_schema_pruning_keeps_relevant_and_floor_and_always():
    def sch(name, desc):
        return {"type": "function", "function": {"name": name, "description": desc,
                                                 "parameters": {"type": "object", "properties": {}}}}
    schemas = [
        sch("fs_read", "прочитать файл из рабочей папки"),
        sch("fs_write", "записать файл"),
        sch("shell_run", "выполнить команду в песочнице"),
        sch("git_commit", "сделать git commit"),
        sch("gmail_send", "отправить письмо"),
        sch("browser_open", "открыть страницу в браузере"),
        sch("browser_confirmed_click", "клик с подтверждением платежа"),
        sch("media_caption", "подпись к изображению"),
    ]
    pruned = prune_tool_schemas(schemas, "прочитать файл и записать файл", keep_min=3,
                                always=("browser_confirmed_click",))
    names = [s["function"]["name"] for s in pruned]
    # релевантные оставлены
    assert "fs_read" in names and "fs_write" in names
    # always-инструмент безопасности сохранён
    assert "browser_confirmed_click" in names
    # обрезка реально произошла
    assert len(pruned) < len(schemas)
    # нерелевантный gmail отброшен
    assert "gmail_send" not in names
