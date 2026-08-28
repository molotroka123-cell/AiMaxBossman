"""V2.1 фаза E — Память/Obsidian через канонический tool-loop.

Проверяется РЕАЛЬНОЕ поведение на настоящем временном vault'е:
модель задаёт вопрос → сама вызывает `memory.search` → получает фрагмент нужной
заметки → отвечает со ссылкой на источник. Плюс запись только в `BOSSMAN Memory/`,
инкрементальная переиндексация, отказ на запись наружу и бюджет пакета памяти.

Встроенный backend — `LocalMemoryBackend` (BM25 на stdlib): внешнего бинаря
`memsearch` в среде нет, и тесты его не требуют.
"""
import asyncio
from pathlib import Path

import pytest

from bcc.providers import ChatResult, ToolCall
from bcc.tools import REGISTRY
from bcc.v2.memory import (
    LexicalReranker,
    LocalMemoryBackend,
    MemoryHit,
    ObsidianMemoryService,
    ObsidianVault,
    build_context_pack,
    chunk_markdown,
)

from .conftest import FakeAdapter, wait_for
from .helpers import make_stack

ARCHITECTURE = """---
title: Архитектура сервиса
---
# Архитектура сервиса

## Выбор базы данных

Мы выбрали PostgreSQL как основное хранилище: нужны транзакции и JSONB.
Redis остаётся только кэшем и никогда не считается источником правды.

## Очереди задач

Очереди держим в самой базе, отдельный брокер не вводим.
"""

CODESTYLE = """# Кодстайл проекта

Пишем на Python, отступ четыре пробела, строка не длиннее ста символов.
Комментарии по-русски.
"""

RETRO = """# Ретроспектива спринта

Провалились с деплоем в пятницу. Договорились: релизы только по вторникам.
"""


@pytest.fixture
def vault_dir(tmp_path) -> Path:
    """Настоящий маленький vault: две папки заметок и приватный .obsidian."""
    root = tmp_path / "vault"
    (root / "notes").mkdir(parents=True)
    (root / ".obsidian").mkdir()
    (root / ".obsidian" / "workspace.md").write_text(
        "приватный конфиг Obsidian, PostgreSQL", encoding="utf-8")
    (root / "notes" / "architecture.md").write_text(ARCHITECTURE, encoding="utf-8")
    (root / "notes" / "codestyle.md").write_text(CODESTYLE, encoding="utf-8")
    (root / "notes" / "retro.md").write_text(RETRO, encoding="utf-8")
    return root


def _service(vault_dir: Path, tmp_path: Path) -> ObsidianMemoryService:
    vault = ObsidianVault(root=vault_dir)
    backend = LocalMemoryBackend(index_path=tmp_path / "idx" / "index.json",
                                 vault_root=vault_dir)
    return ObsidianMemoryService(vault=vault, backend=backend,
                                 reranker=LexicalReranker())


# ---------- стенд модели (стиль tests/test_v21_tool_loop.py) ----------

class ToolAdapter(FakeAdapter):
    """Модель по сценарию: ("tool", имя, аргументы) | ("text", ответ) | ("cite",).

    Шаг "cite" строит ответ ИЗ полученного tool-сообщения — так видно, что модель
    реально получила содержимое заметки, а не выдумала его.
    """

    def __init__(self, script, **kw):
        super().__init__(**kw)
        self.script = list(script)
        self.seen_messages: list[list[dict]] = []
        self.seen_tools: list = []

    async def chat(self, model, messages, **kw):
        self.calls += 1
        self.seen_messages.append([dict(m) for m in messages])
        self.seen_tools.append(kw.get("tools"))
        step = self.script[min(self.calls - 1, len(self.script) - 1)]
        if step[0] == "tool":
            return ChatResult(text="", tokens_in=5, tokens_out=2, finish="tool_calls",
                              model=model,
                              tool_calls=[ToolCall(id=f"call_{self.calls}", name=step[1],
                                                   arguments=step[2],
                                                   raw_arguments="{}")])
        if step[0] == "cite":
            tool_msg = next((m for m in reversed(messages) if m.get("role") == "tool"), None)
            body = str(tool_msg.get("content") if tool_msg else "")
            source = ""
            for line in body.splitlines():
                if "источник:" in line:
                    source = line.split("источник:", 1)[1].split("|")[0].strip()
                    break
            db = "PostgreSQL" if "PostgreSQL" in body else "неизвестно"
            return ChatResult(text=f"Выбрана {db} (источник: {source})",
                              tokens_in=5, tokens_out=6, model=model)
        return ChatResult(text=step[1], tokens_in=5, tokens_out=3, model=model)


TERMINAL = ("completed", "failed", "stopped", "waiting_approval")
FINISHED = ("completed", "failed", "stopped")


async def _run_task(env, task_id, *, timeout=8.0, until=TERMINAL):
    env.svc.engine.poll_interval = 0.02
    worker = asyncio.create_task(env.svc.engine.worker_loop())
    watcher = asyncio.create_task(env.svc.engine.approval_watcher())
    try:
        async def done():
            t = (await env.client.get(f"/api/tasks/{task_id}")).json()
            status = t["task"]["status"] if "task" in t else t["status"]
            return status if status in until else None
        return await wait_for(done, timeout=timeout)
    finally:
        worker.cancel()
        watcher.cancel()
        await asyncio.gather(worker, watcher, return_exceptions=True)


async def _configure(env, vault_dir: Path, **extra):
    r = await env.client.post("/api/memory/config",
                              json={"root": str(vault_dir), **extra})
    assert r.status_code == 200, r.text
    return r.json()


async def _stack(env, tools, *, adapter=None, permissions=None, max_steps=4,
                 prompt="какую базу данных мы выбрали?"):
    stack = await make_stack(env.client, max_steps=max_steps, prompt=prompt)
    if adapter is not None:
        env.svc.registry.adapter_factory = lambda m, p: adapter
    patch = {"tools": tools}
    if permissions is not None:
        patch["permissions"] = permissions
    await env.client.patch(f"/api/agents/{stack['agent']['id']}", json=patch)
    return stack


# ---------- 1-2. настоящий vault, индексация ----------

async def test_config_is_explicit_and_index_reports_files(env, vault_dir):
    """Путь к vault задаёт человек; автопоиска нет. Индекс видит только заметки."""
    before = (await env.client.get("/api/memory/config")).json()
    assert before["configured"] is False

    cfg = await _configure(env, vault_dir)
    assert cfg["configured"] and cfg["root"] == str(vault_dir)
    # V2.2: backend=local — алиас на производный SQLite-индекс. Источник истины
    # по-прежнему markdown; JSON-бэкенд остался откатом под backend=local-json.
    assert cfg["backend_class"] == "SQLiteMemoryBackend"

    res = (await env.client.post("/api/memory/index", json={})).json()["result"]
    assert res["files"] == 3 and res["added"] == 3 and res["chunks"] >= 4

    stats = (await env.client.get("/api/memory/stats")).json()["stats"]
    assert stats["backend"] == "sqlite" and stats["files"] == 3

    # приватный .obsidian не проиндексирован
    hits = (await env.client.post("/api/memory/search",
                                  json={"query": "приватный конфиг Obsidian"})).json()
    assert all(".obsidian" not in i["source"] for i in hits["items"])


async def test_memsearch_backend_fails_honestly_when_binary_missing(env, vault_dir):
    """Внешний memsearch не установлен → честный отказ, а не тихая подмена."""
    bad = await env.client.post("/api/memory/config",
                                json={"root": str(vault_dir), "backend": "memsearch"})
    assert bad.status_code == 400
    assert "memsearch" in bad.json()["error"]["message"]
    # битый конфиг не сохранён
    assert (await env.client.get("/api/memory/config")).json()["configured"] is False


async def test_search_finds_the_right_note(env, vault_dir):
    await _configure(env, vault_dir)
    await env.client.post("/api/memory/index", json={})
    body = (await env.client.post("/api/memory/search",
                                  json={"query": "какую базу данных выбрали"})).json()
    assert body["items"], "поиск ничего не нашёл"
    top = body["items"][0]
    assert top["source"] == "notes/architecture.md"
    assert "PostgreSQL" in top["content"]
    assert "Выбор базы данных" in top["heading"]


# ---------- 3-5. модель сама вызывает инструмент и отвечает со ссылкой ----------

async def test_model_calls_memory_search_and_cites_source(env, vault_dir):
    """Главный тест фазы E: вопрос → memory.search → нужная заметка → ответ с источником."""
    await _configure(env, vault_dir)
    await env.client.post("/api/memory/index", json={})

    adapter = ToolAdapter([("tool", "memory_search", {"query": "какую базу данных выбрали"}),
                           ("cite",)])
    stack = await _stack(env, ["memory.*"], adapter=adapter)

    assert await _run_task(env, stack["task"]["id"]) == "completed"
    assert adapter.calls == 2

    # схемы памяти реально ушли провайдеру
    names = [t["function"]["name"] for t in adapter.seen_tools[0]]
    assert "memory_search" in names and "memory_write" in names

    tool_msg = adapter.seen_messages[1][-1]
    assert tool_msg["role"] == "tool"
    assert "PostgreSQL" in tool_msg["content"]
    assert "notes/architecture.md" in tool_msg["content"]
    # содержимое vault подано как ВНЕШНИЕ ДАННЫЕ, а не как команды
    assert tool_msg["content"].startswith("Ниже — внешние данные")

    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()
    result = task["runs"][-1]["result"]
    assert "PostgreSQL" in result and "notes/architecture.md" in result


async def test_memory_is_not_injected_into_every_call(env, vault_dir):
    """Требование: никакой автоматической инъекции памяти в каждый вызов модели."""
    await _configure(env, vault_dir)
    await env.client.post("/api/memory/index", json={})

    adapter = ToolAdapter([("text", "отвечаю без памяти")])
    stack = await _stack(env, ["memory.*"], adapter=adapter)
    assert await _run_task(env, stack["task"]["id"]) == "completed"

    blob = "\n".join(str(m.get("content") or "") for m in adapter.seen_messages[0])
    assert "PostgreSQL" not in blob and "architecture.md" not in blob


# ---------- 6. запись только в BOSSMAN Memory ----------

async def test_memory_write_creates_note_only_in_bossman_folder(env, vault_dir):
    """Право filesystem.write выдано → запись идёт без вопроса, но только в свою папку."""
    await _configure(env, vault_dir)
    await env.client.post("/api/memory/index", json={})

    adapter = ToolAdapter([("tool", "memory_write",
                            {"title": "Релизы по вторникам", "kind": "decision",
                             "content": "Деплой в пятницу запрещён, релиз — вторник."}),
                           ("text", "записал решение в память")])
    stack = await _stack(env, ["memory.*"], adapter=adapter,
                         permissions={"filesystem.write": True},
                         prompt="запомни решение о релизах")

    assert await _run_task(env, stack["task"]["id"], until=FINISHED) == "completed"

    write_root = vault_dir / "BOSSMAN Memory"
    created = list(write_root.glob("*.md"))
    assert len(created) == 1
    text = created[0].read_text(encoding="utf-8")
    assert "Деплой в пятницу запрещён" in text and "kind: decision" in text

    # нигде больше ничего не появилось
    others = [p for p in vault_dir.rglob("*.md") if write_root not in p.parents]
    assert sorted(p.name for p in others) == ["architecture.md", "codestyle.md",
                                              "retro.md", "workspace.md"]


async def test_memory_write_asks_approval_without_permission(env, vault_dir):
    """Без права filesystem.write запись в чужой vault идёт через подтверждение."""
    await _configure(env, vault_dir)
    adapter = ToolAdapter([("tool", "memory_write",
                            {"title": "Заметка", "content": "тело"}),
                           ("text", "готово")])
    stack = await _stack(env, ["memory.*"], adapter=adapter, prompt="запомни")

    assert await _run_task(env, stack["task"]["id"]) == "waiting_approval"
    assert not (vault_dir / "BOSSMAN Memory").exists() or \
        not list((vault_dir / "BOSSMAN Memory").glob("*.md"))
    appr = (await env.client.get("/api/approvals")).json()
    assert len(appr) == 1 and "memory.write" in appr[0]["preview"]


# ---------- 7. инкрементальная переиндексация ----------

async def test_new_note_is_searchable_after_incremental_reindex(env, vault_dir):
    await _configure(env, vault_dir)
    first = (await env.client.post("/api/memory/index", json={})).json()["result"]
    assert first["added"] == 3

    written = (await env.client.post("/api/memory/write", json={
        "title": "Мониторинг", "kind": "decision",
        "content": "Метрики собираем через Prometheus, дашборды в Grafana."})).json()
    assert "BOSSMAN Memory" in written["path"]

    found = (await env.client.post("/api/memory/search",
                                   json={"query": "чем собираем метрики Prometheus"})).json()
    assert found["items"] and "BOSSMAN Memory" in found["items"][0]["source"]
    assert "Prometheus" in found["items"][0]["content"]

    # повторный проход не перечитывает неизменившиеся файлы
    again = (await env.client.post("/api/memory/index", json={})).json()["result"]
    assert again["added"] == 0 and again["updated"] == 0 and again["skipped"] == 4

    # правка существующей заметки подхватывается
    (vault_dir / "notes" / "retro.md").write_text(
        RETRO + "\n## Постмортем\n\nПричина — отсутствие канареечного выката.\n",
        encoding="utf-8")
    third = (await env.client.post("/api/memory/index", json={})).json()["result"]
    assert third["updated"] == 1 and third["skipped"] == 3
    canary = (await env.client.post("/api/memory/search",
                                    json={"query": "канареечный выкат"})).json()
    assert canary["items"] and canary["items"][0]["source"] == "notes/retro.md"


# ---------- 8. запись наружу запрещена ----------

def test_write_outside_write_root_is_refused(vault_dir):
    vault = ObsidianVault(root=vault_dir)
    with pytest.raises(PermissionError):
        vault.write_memory(title="побег", content="x", filename="../../evil.md")
    with pytest.raises(PermissionError):
        vault.write_memory(title="побег", content="x", filename="/tmp/evil.md")
    with pytest.raises(PermissionError):
        vault.write_memory(title="побег", content="x", filename="../notes/evil.md")
    assert not (vault_dir / "notes" / "evil.md").exists()


async def test_tool_refuses_write_outside_and_run_survives(env, vault_dir):
    """Отказ приходит модели ДАННЫМИ: run не падает, файл не создан."""
    await _configure(env, vault_dir)
    adapter = ToolAdapter([("tool", "memory_write",
                            {"title": "побег", "content": "x",
                             "filename": "../../evil.md"}),
                           ("text", "запись отклонена, ничего не сохранил")])
    stack = await _stack(env, ["memory.*"], adapter=adapter,
                         permissions={"filesystem.write": True}, prompt="сохрани")

    assert await _run_task(env, stack["task"]["id"], until=FINISHED) == "completed"
    assert "отклонён" in adapter.seen_messages[1][-1]["content"]
    assert not (vault_dir.parent / "evil.md").exists()
    assert not (vault_dir / "notes" / "evil.md").exists()


# ---------- 9. бюджет пакета памяти ----------

def test_context_pack_respects_token_budget_and_dedup():
    hits = [MemoryHit("одинаковый текст " * 200, "a.md", score=1.0, chunk_hash="h1"),
            MemoryHit("одинаковый текст " * 200, "b.md", score=0.9, chunk_hash="h2"),
            MemoryHit("другой длинный текст " * 200, "c.md", score=0.8, chunk_hash="h3"),
            MemoryHit("третий длинный текст " * 200, "d.md", score=0.7, chunk_hash="h4")]
    pack = build_context_pack("q", hits, max_tokens=800, max_items=8, per_item_tokens=300)
    assert pack.estimated_tokens <= 800
    assert [i.source for i in pack.items] == ["a.md", "c.md"] or len(pack.items) <= 3
    assert len({i.content for i in pack.items}) == len(pack.items)   # дублей нет


async def test_search_tool_keeps_memory_budget_separate(env, vault_dir, tmp_path):
    """Бюджет памяти ограничивает только пакет памяти и соблюдается инструментом."""
    big = "\n\n".join(f"## Раздел {i}\n\n" + ("наблюдение про деплой " * 120)
                      for i in range(12))
    (vault_dir / "notes" / "big.md").write_text("# Большая заметка\n\n" + big,
                                                encoding="utf-8")
    await _configure(env, vault_dir)
    await env.client.post("/api/memory/index", json={})

    tight = (await env.client.post("/api/memory/search",
                                   json={"query": "наблюдение про деплой",
                                         "max_context_tokens": 500})).json()
    wide = (await env.client.post("/api/memory/search",
                                  json={"query": "наблюдение про деплой",
                                        "max_context_tokens": 6000})).json()
    assert tight["estimated_tokens"] <= 500
    assert wide["estimated_tokens"] <= 6000
    assert wide["estimated_tokens"] > tight["estimated_tokens"]
    assert len(wide["items"]) >= len(tight["items"])


# ---------- 10. прогрессивное раскрытие и реестр ----------

async def test_expand_returns_full_section(env, vault_dir):
    await _configure(env, vault_dir)
    await env.client.post("/api/memory/index", json={})
    found = (await env.client.post("/api/memory/search",
                                   json={"query": "очереди задач брокер"})).json()
    ch = found["items"][0]["chunk_hash"]
    detail = (await env.client.post("/api/memory/expand", json={"chunk_hash": ch})).json()
    assert detail["source"] == "notes/architecture.md"
    assert "брокер не вводим" in detail["content"]

    bad = await env.client.post("/api/memory/expand", json={"chunk_hash": "нет-такого"})
    assert bad.status_code == 404


async def test_memory_tools_are_registered_canonically(env, vault_dir):
    names = {"memory.search", "memory.expand", "memory.write", "memory.index",
             "memory.stats"}
    assert names <= set(REGISTRY.names())
    for name in names:
        assert REGISTRY.get(name).source == "memory"
    assert REGISTRY.get("memory.search").default_effect == "auto"
    assert REGISTRY.get("memory.search").external_output is True
    assert REGISTRY.get("memory.expand").external_output is True
    assert REGISTRY.get("memory.write").default_effect == "ask"
    assert REGISTRY.get("memory.write").permission == "filesystem.write"
    assert REGISTRY.get("memory.write").idempotent is False
    # имя для модели без точек
    assert REGISTRY.get("memory.search").api_name == "memory_search"


async def test_tools_report_honestly_when_vault_not_configured(env):
    """Без настроенного vault инструмент отвечает ошибкой-данными, а не падает."""
    adapter = ToolAdapter([("tool", "memory_search", {"query": "что угодно"}),
                           ("text", "память не настроена")])
    stack = await _stack(env, ["memory.*"], adapter=adapter)
    assert await _run_task(env, stack["task"]["id"]) == "completed"
    assert "память недоступна" in adapter.seen_messages[1][-1]["content"]


# ---------- 11. чанкование ----------

def test_chunking_splits_by_heading():
    chunks, sections = chunk_markdown(ARCHITECTURE, "notes/architecture.md")
    heads = [c.heading for c in chunks]
    assert "Архитектура сервиса > Выбор базы данных" in heads
    assert "Архитектура сервиса > Очереди задач" in heads
    assert all(c.source == "notes/architecture.md" for c in chunks)
    assert len(sections) == len(set(c.section_id for c in chunks))
