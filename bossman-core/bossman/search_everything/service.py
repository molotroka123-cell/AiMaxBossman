"""SearchService — подсистема этапа 5 (Subsystem, name='search_everything').

Держит долгоживущий ContextEngine на процесс (get_engine, per INTEGRATION_GUIDE),
регистрирует search.*-инструменты, отдаёт единый поиск роутеру/инструментам.
validate/start/stop идемпотентны и degrade-safe: подсистема некритична
(critical=False) — её сбой не роняет ядро, а лишь помечает degraded.

ЕДИНЫЙ стор: SearchService переиспользует context_engine SQLite-индекс. Второго
RAG/векторного стора нет; get_engine закрывает движок на общем shutdown ядра —
поэтому stop() НЕ закрывает shared-движок (иначе двойное закрытие).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .. import errors, events
from ..config import settings
from ..context_engine import ContextEngine
from ..context_engine.models import Document
from ..context_engine.service import get_engine
from ..obs import get_logger
from .connectors import SecretPolicy, filesystem_documents
from .engine import DEFAULT_ALLOW, SafeReranker, SearchDocument, SearchEngine, SearchHit

log = get_logger("bossman.search.service")


class SearchService:
    """Фасад единого поиска + жизненный цикл подсистемы."""

    name = "search_everything"
    critical = False

    def __init__(self, engine: ContextEngine | None = None, *, db_path: str | Path | None = None,
                 policy: SecretPolicy | None = None,
                 sensitivity_allow: Iterable[str] = DEFAULT_ALLOW) -> None:
        self._db_path = db_path if db_path is not None else settings.context_db
        self.policy = policy or SecretPolicy()
        self._default_allow = tuple(sensitivity_allow)
        self._search: SearchEngine | None = None
        self._started = False
        if engine is not None:
            self._search = SearchEngine(engine, policy=self.policy,
                                        sensitivity_allow=self._default_allow)

    # ---- ленивое получение адаптера над общим движком -------------------------
    def _ensure(self) -> SearchEngine:
        if self._search is None:
            eng = get_engine(str(self._db_path), reranker=SafeReranker())
            self._search = SearchEngine(eng, policy=self.policy,
                                        sensitivity_allow=self._default_allow)
        return self._search

    @property
    def engine(self) -> ContextEngine:
        return self._ensure().engine

    # ---- индексация -----------------------------------------------------------
    def index_text(self, text: str, *, source_uri: str, source_type: str = "text",
                   project: str = "", metadata: dict | None = None,
                   sensitivity: str = "normal") -> Document | None:
        meta = dict(metadata or {})
        meta.setdefault("sensitivity", sensitivity)
        doc = SearchDocument(source_uri, text, source_type, project or None, meta)
        got = self._ensure().upsert([doc])
        return got[0] if got else None

    def index_documents(self, docs: Iterable[SearchDocument]) -> dict[str, int]:
        eng = self._ensure()
        docs = list(docs)
        indexed = eng.upsert(docs)
        # refused = секреты (не вошли в стор); skipped = неизменённые/пустые.
        indexed_ids = {d.source_uri for d in indexed}
        refused = sum(1 for d in docs if self.policy.is_secret(path=d.id, text=d.text))
        return {"indexed": len(indexed), "refused": refused,
                "skipped": len(docs) - len(indexed) - refused,
                "ingested_ids": len(indexed_ids)}

    def index_tree(self, root: str | Path, *, project: str = "") -> dict[str, int]:
        docs = filesystem_documents(root, project=project or None, policy=self.policy)
        return self.index_documents(docs)

    # ---- поиск ----------------------------------------------------------------
    def search(self, query: str, *, project: str = "", limit: int = 10,
               sensitivity_allow: Iterable[str] | None = None) -> list[SearchHit]:
        return self._ensure().search(query, project=project or None, limit=limit,
                                     sensitivity_allow=sensitivity_allow)

    def telemetry(self) -> dict[str, Any]:
        return self._ensure().engine.telemetry()

    # ---- жизненный цикл подсистемы -------------------------------------------
    async def validate(self) -> None:
        # Проверяем доступность единого стора (создаст схему, если её нет).
        try:
            self.telemetry()
        except Exception as exc:  # noqa: BLE001
            raise errors.SearchFailed(f"context store unavailable: {type(exc).__name__}") from exc

    async def start(self) -> None:
        if self._started:
            return
        from .tools import register_tools
        register_tools()
        set_active_service(self)
        self._started = True
        try:
            events.emit("search.ready", subsystem=self.name, **self.telemetry())
        except Exception:  # noqa: BLE001 — телеметрия/событие не должны ронять start
            pass
        log.info("search: подсистема запущена (единый стор context_engine)")

    async def stop(self) -> None:
        # Идемпотентно. Общий движок НЕ закрываем (его владелец —
        # context_engine.close_all на shutdown ядра): избегаем двойного close.
        if get_active_service() is self:
            set_active_service(None)
        self._started = False


# --- активный сервис процесса (для инструментов и роутера) --------------------

_ACTIVE: SearchService | None = None


def set_active_service(service: SearchService | None) -> None:
    global _ACTIVE
    _ACTIVE = service


def get_active_service() -> SearchService | None:
    return _ACTIVE


def get_service() -> SearchService:
    """Активный сервис или ленивое построение дефолтного на общем сторе."""
    global _ACTIVE
    if _ACTIVE is None:
        _ACTIVE = SearchService(db_path=settings.context_db)
    return _ACTIVE


def build_subsystem() -> SearchService:
    """Фабрика подсистемы для api.py. Degrade-safe: не открывает стор и не
    падает на импорте — validate/start отработают в registry.start_all()."""
    try:
        from .tools import register_tools
        register_tools()
    except Exception as exc:  # noqa: BLE001 — регистрация инструментов не критична для boot
        log.warning("search: register_tools отложена (%s)", exc)
    return SearchService(db_path=settings.context_db)
