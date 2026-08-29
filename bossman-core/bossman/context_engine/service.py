"""Интеграционный слой ЭТАП 2.222 поверх реального ядра bossman-core.

Здесь context_engine подключается КАК СЛОЙ к существующему bossman.context.
ContextBuilder (раздел 10 ТЗ), а не как его замена. ContextBuilder по-прежнему
собирает блоки под KV-кэш llama.cpp и считает токены; движок лишь наполняет его
блок `retrieved` долговременной памятью (с provenance) и evidence-чанками
(с source-refs), которые раньше были пустыми. Так сохраняется единственный
memory-стек, а не два параллельных.

Долгоживущие объекты (store/embedder/retriever/memory/compiler/compact)
создаются один раз на процесс через get_engine(), а не на каждый запрос.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Protocol

from .compact import CompactResult, CompactSkill, Message
from .compiler import ContextCompiler
from .embeddings import Embedder, HashEmbedder
from .ingest import Ingestor
from .memory import MemoryManager, MemoryPlugin, StoreMemoryPlugin
from .models import MemoryStatus
from .retrieval import HybridRetriever, Reranker
from .store import ContextStore


class _BuilderLike(Protocol):
    def set_retrieved(self, chunks: list[str]) -> None: ...


class ContextEngine:
    """Фасад над store/retrieval/memory/compiler/compact для одного процесса."""

    def __init__(self, db_path: str | Path, *, embedder: Embedder | None = None,
                 reranker: Reranker | None = None,
                 memory_plugins: list[MemoryPlugin] | None = None) -> None:
        self.store = ContextStore(db_path)
        self.embedder = embedder or HashEmbedder(384)
        self.retriever = HybridRetriever(self.store, self.embedder, reranker)
        self.memory = MemoryManager(self.store, plugins=memory_plugins or [StoreMemoryPlugin(self.store)])
        self.compiler = ContextCompiler(self.retriever, self.memory)
        self.compact_skill = CompactSkill(self.memory.plugins)
        self.ingestor = Ingestor(self.store, self.embedder)

    # ---- индексирование источников ----
    def index_text(self, text: str, *, source_uri: str, source_type: str = "text",
                   project: str = "", metadata: dict | None = None, sensitivity: str = "normal"):
        return self.ingestor.ingest_text(text, source_uri=source_uri, source_type=source_type,
                                         project=project, metadata=metadata, sensitivity=sensitivity)

    def index_tree(self, root: str | Path, *, project: str = ""):
        return self.ingestor.ingest_tree(root, project=project)

    # ---- блоки для существующего ContextBuilder ----
    def memory_block(self, query: str, project: str = "", *, limit: int = 8) -> str:
        mems = self.memory.retrieve(query, project=project, limit=limit)
        if not mems:
            return ""
        lines = []
        for m in mems:
            label = f"{m.kind.value}/{m.status.value}/{m.memory_id}"
            disputed = " [DISPUTED]" if m.status == MemoryStatus.DISPUTED else ""
            src = ",".join(m.source_refs)
            lines.append(f"- [{label}]{disputed} {m.text} | sources={src}")
        return "## Долговременная память (provenance)\n" + "\n".join(lines)

    def evidence_blocks(self, query: str, project: str = "", *, max_blocks: int = 6) -> list[str]:
        hits = self.retriever.search(query, project=project, result_limit=max_blocks)
        blocks = []
        for h in hits:
            head = h.chunk.heading or h.chunk.chunk_id
            blocks.append(f"### {h.chunk.source_uri} :: {head}\n{h.chunk.text}\n"
                          f"[source={h.chunk.chunk_id}; score={h.final_score:.3f}]")
        return blocks

    def build_injection(self, query: str, project: str = "", *,
                        max_blocks: int = 6, memory_limit: int = 8) -> list[str]:
        """Список строк для ContextBuilder.set_retrieved: durable memory впереди
        evidence-чанков — память приоритетнее и не вытесняется чанками."""
        out: list[str] = []
        mem = self.memory_block(query, project, limit=memory_limit)
        if mem:
            out.append(mem)
        out.extend(self.evidence_blocks(query, project, max_blocks=max_blocks))
        return out

    def inject_into_builder(self, builder: _BuilderLike, query: str, project: str = "", *,
                            max_blocks: int = 6, memory_limit: int = 8) -> list[str]:
        """Точка интеграции: наполняет блок retrieved реального ContextBuilder.

        Ошибка движка/ранкера деградирует к пустой инъекции, а не роняет агента.
        """
        try:
            blocks = self.build_injection(query, project, max_blocks=max_blocks, memory_limit=memory_limit)
        except Exception:
            blocks = []
        builder.set_retrieved(blocks)
        return blocks

    # ---- compact ----
    def compact(self, messages: list[Message], *, project: str = "", target_tokens: int = 6000,
                keep_recent: int = 8, query: str = "") -> CompactResult:
        return self.compact_skill.compact(messages, project=project, target_tokens=target_tokens,
                                          keep_recent=keep_recent, query=query)

    # ---- телеметрия/жизненный цикл ----
    def telemetry(self) -> dict:
        db = self.store.db
        docs = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        mems = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        try:
            size = Path(self.store.path).stat().st_size
        except OSError:
            size = 0
        return {"documents": docs, "chunks": chunks, "memories": mems, "db_bytes": size}

    def close(self) -> None:
        self.store.close()


# --- tool schema pruning -----------------------------------------------------

_WORD = re.compile(r"[\w]{2,}", re.UNICODE)


def _schema_terms(schema: dict) -> set[str]:
    fn = schema.get("function", schema)
    text = f"{fn.get('name','')} {fn.get('description','')}"
    params = (fn.get("parameters") or {}).get("properties") or {}
    text += " " + " ".join(params.keys())
    return {w.lower() for w in _WORD.findall(text)}


def prune_tool_schemas(schemas: list[dict], query: str, *, keep_min: int = 6,
                       always: tuple[str, ...] = ()) -> list[dict]:
    """Отдать модели только релевантные задаче tools — это уменьшает
    system/tool-контекст без сжатия пользовательских данных (tool schema pruning).

    Инструменты из `always` (например критичные к безопасности confirmed_*)
    сохраняются всегда. Если релевантных мало, добавляется floor keep_min, чтобы
    не оставить агента без инструментов.
    """
    if len(schemas) <= keep_min:
        return list(schemas)
    q = {w.lower() for w in _WORD.findall(query)}
    scored: list[tuple[float, int, dict]] = []
    for i, s in enumerate(schemas):
        name = (s.get("function", s) or {}).get("name", "")
        overlap = len(q & _schema_terms(s)) / max(1, len(q)) if q else 0.0
        scored.append((overlap, i, s))
    scored.sort(key=lambda x: (-x[0], x[1]))
    kept: list[tuple[int, dict]] = []
    seen: set[int] = set()
    for score, i, s in scored:
        name = (s.get("function", s) or {}).get("name", "")
        if score > 0 or name in always or len(kept) < keep_min:
            kept.append((i, s)); seen.add(i)
    # гарантируем always-инструменты, даже если не попали по score/floor
    for i, s in enumerate(schemas):
        name = (s.get("function", s) or {}).get("name", "")
        if name in always and i not in seen:
            kept.append((i, s)); seen.add(i)
    kept.sort(key=lambda x: x[0])
    return [s for _, s in kept]


# --- process-level singleton -------------------------------------------------

_ENGINES: dict[str, ContextEngine] = {}
_LOCK = threading.Lock()


def get_engine(db_path: str | Path, **kwargs) -> ContextEngine:
    """Долгоживущий движок на процесс, ключ — путь к БД."""
    key = str(Path(db_path))
    with _LOCK:
        eng = _ENGINES.get(key)
        if eng is None:
            eng = ContextEngine(db_path, **kwargs)
            _ENGINES[key] = eng
        return eng


def close_all() -> None:
    with _LOCK:
        for eng in _ENGINES.values():
            try:
                eng.close()
            except Exception:
                pass
        _ENGINES.clear()
