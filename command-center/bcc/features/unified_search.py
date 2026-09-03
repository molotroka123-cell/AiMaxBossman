"""Unified Search — один запрос по всему, что система уже записала, флаг OFF.

BOSSMAN_UNIFIED_SEARCH_ENABLED=1 включает; иначе GET /api/search честно отдаёт
{"enabled": false} и НИ ОДНОГО запроса в БД не делает — выключенный флаг обязан
означать «приложение ведёт себя ровно как до этого модуля».

Зачем модуль вообще нужен. Механика поиска в проекте есть, но она разбросана:
images ищет по своим ассетам, openrouter — по каталогу моделей, context_os — по
таблице failures. Каждая из них знает про одну таблицу и не показывает
происхождение строки. Владельцу нужна одна точка входа: «где я это видел» —
поэтому здесь собственный слой поверх bcc/db, а не обёртка над той или иной
частной ручкой. Существующие поиски не трогаются (чужие файлы).

Три вещи, которые здесь важнее удобства:

  происхождение — у каждой строки есть source (имя таблицы), id, время и path,
                  по которому строку можно открыть; результат без происхождения
                  бесполезен, потому что владелец не может его проверить;
  экранирование — пользовательский ввод уходит только параметром, а спецсимволы
                  LIKE (процент, подчёркивание, обратный слэш) экранируются:
                  иначе запрос «%_%» вернёт всю
                  базу и это будет выглядеть как удачный поиск, хотя поиска
                  не было;
  честное усечение — предел на каждый источник И общий, добор по кругу, чтобы
                  один шумный источник (events) не вытеснил остальные; всё, что
                  обрезано, помечено truncated. Тихое усечение читается как
                  «нашлось всё» и врёт владельцу.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import (approvals as approvals_t, events as events_t, missions as missions_t,
                  run_events as run_events_t, task_runs as runs_t, tasks as tasks_t)
from ..plugin_security import redact_text
from . import Feature

FLAG = "BOSSMAN_UNIFIED_SEARCH_ENABLED"
router = APIRouter()

# Слишком короткий запрос — это не поиск, а выгрузка базы: одна буква совпадёт
# почти со всем, и владелец примет шум за результат.
MIN_QUERY_CHARS = 2
MAX_QUERY_CHARS = 200
DEFAULT_PER_SOURCE = 20
MAX_PER_SOURCE = 100
DEFAULT_TOTAL = 100
MAX_TOTAL = 500
EXCERPT_WIDTH = 180


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Source:
    """Описание источника: откуда искать, чем датировать и куда вести владельца.

    time_columns — цепочка coalesce, а не одна колонка: у прогона осмысленное
    время появляется только с finished_at/started_at, и брать первое непустое
    честнее, чем показывать пустоту.
    """

    name: str                      # имя таблицы; оно же уходит в ответ как source
    table: sa.Table
    fields: tuple[str, ...]        # колонки, по которым ищем
    time_columns: tuple[str, ...]
    path: str                      # шаблон ссылки, форматируется полями строки
    link_fields: tuple[str, ...] = field(default=())   # колонки только для ссылки


# Четыре обязательных источника (события, прогоны, задачи, подтверждения) плюс
# миссии и построчный лог прогона: владелец ищет по смыслу, а не по таблицам.
SOURCES: tuple[Source, ...] = (
    Source("events", events_t, ("kind", "data"), ("ts",), "/api/activity"),
    Source("tasks", tasks_t, ("title", "prompt"), ("created_at",), "/api/tasks/{id}"),
    Source("task_runs", runs_t, ("result", "error", "model_alias"),
           ("finished_at", "started_at"), "/api/runs/{id}"),
    Source("run_events", run_events_t, ("kind", "message"), ("ts",),
           "/api/runs/{run_id}/events", ("run_id",)),
    Source("approvals", approvals_t, ("kind", "preview"), ("decided_at", "created_at"),
           "/api/approvals/{id}"),
    Source("missions", missions_t, ("title", "goal"),
           ("finished_at", "started_at", "created_at"), "/api/missions/{id}"),
)


def escape_like(value: str) -> str:
    """Экранировать спецсимволы LIKE. Обратный слэш — первым, иначе экранируем
    собственное экранирование и «\\%» снова станет джокером."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _as_text(value: Any) -> str:
    """Значение колонки в текст: JSON-поля (events.data) ищутся и показываются
    как сериализованный JSON — иначе половина ленты активности невидима."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _excerpt(text: str, needle: str) -> str:
    """Окно вокруг найденного места. redact_text — потому что в result/error
    прогона может оказаться токен из ответа модели, а выдержка уходит в UI."""
    pos = text.lower().find(needle)
    if pos < 0:                                  # совпало в другой колонке
        head = text[:EXCERPT_WIDTH]
        return redact_text(head + ("…" if len(text) > EXCERPT_WIDTH else ""))
    start = max(0, pos - EXCERPT_WIDTH // 3)
    end = min(len(text), pos + len(needle) + (EXCERPT_WIDTH * 2) // 3)
    frag = ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")
    return redact_text(frag)


def _time_expr(src: Source):
    cols = [src.table.c[c] for c in src.time_columns]
    return cols[0] if len(cols) == 1 else sa.func.coalesce(*cols)


async def _search_source(svc, src: Source, needle: str, per_source: int) -> tuple[list[dict], bool]:
    """Строки одного источника + признак «источник дал больше, чем предел».

    Тянем per_source + 1 строку: лишняя нужна не для выдачи, а чтобы отличить
    «ровно предел» от «обрезано», не делая второй запрос COUNT по тем же LIKE.
    """
    pattern = f"%{escape_like(needle)}%"
    ts = _time_expr(src).label("_ts")
    cols = [src.table.c.id, ts]
    cols += [src.table.c[f] for f in src.fields]
    cols += [src.table.c[f] for f in src.link_fields]
    # Параметризация: pattern уходит bind-параметром, склейки строк в SQL нет нигде.
    where = sa.or_(*[sa.func.lower(sa.cast(src.table.c[f], sa.Text)).like(pattern, escape="\\")
                     for f in src.fields])
    stmt = (sa.select(*cols).where(where)
            .order_by(src.table.c.id.desc()).limit(per_source + 1))
    async with svc.db.session() as s:
        rows = (await s.execute(stmt)).fetchall()

    over = len(rows) > per_source
    out: list[dict] = []
    for row in rows[:per_source]:
        m = row._mapping
        matched, text = "", ""
        for f in src.fields:                     # первая колонка с совпадением — она и цитируется
            value = _as_text(m[f])
            if needle in value.lower():
                matched, text = f, value
                break
        if not matched:                          # LIKE совпал, а substr нет (иной регистр Unicode)
            matched = src.fields[0]
            text = _as_text(m[src.fields[0]])
        link = {"id": m["id"], **{f: m[f] for f in src.link_fields}}
        out.append({
            "source": src.name,
            "id": m["id"],
            "ts": m["_ts"].isoformat() if isinstance(m["_ts"], (datetime, date)) else None,
            "field": matched,
            "excerpt": _excerpt(text, needle),
            "path": src.path.format(**link),
        })
    return out, over


async def search_all(svc, query: str, *, per_source: int = DEFAULT_PER_SOURCE,
                     total: int = DEFAULT_TOTAL) -> dict:
    """Поиск по всем источникам сразу. Общий предел добирается по кругу
    (по строке с каждого источника за проход), чтобы шумный events не съел
    бюджет раньше, чем до задач и подтверждений дойдёт очередь."""
    needle = query.strip().lower()
    per_source = max(1, min(per_source, MAX_PER_SOURCE))
    total = max(1, min(total, MAX_TOTAL))

    fetched: dict[str, list[dict]] = {}
    over_limit: dict[str, bool] = {}
    for src in SOURCES:
        rows, over = await _search_source(svc, src, needle, per_source)
        fetched[src.name] = rows
        over_limit[src.name] = over

    kept: dict[str, list[dict]] = {s.name: [] for s in SOURCES}
    budget, depth = total, 0
    while budget > 0 and any(depth < len(fetched[s.name]) for s in SOURCES):
        for src in SOURCES:
            if budget <= 0:
                break
            rows = fetched[src.name]
            if depth < len(rows):
                kept[src.name].append(rows[depth])
                budget -= 1
        depth += 1

    groups, flat, truncated_sources = [], [], []
    for src in SOURCES:
        cut = over_limit[src.name] or len(kept[src.name]) < len(fetched[src.name])
        if cut:
            truncated_sources.append(src.name)
        groups.append({"source": src.name, "count": len(kept[src.name]), "truncated": cut,
                       "results": kept[src.name]})
        flat.extend(kept[src.name])
    flat.sort(key=lambda r: (r["ts"] is not None, r["ts"] or ""), reverse=True)

    return {
        "enabled": True,
        "query": query.strip(),
        "limits": {"per_source": per_source, "total": total},
        "total": len(flat),
        "truncated": bool(truncated_sources),
        "truncated_sources": truncated_sources,
        "sources": groups,
        "results": flat,
    }


@router.get("/search")
async def search(request: Request, q: str = "", limit: int = DEFAULT_PER_SOURCE,
                 total: int = DEFAULT_TOTAL):
    """Читающая ручка: работает и при выключенном флаге, но тогда не ищет."""
    if not enabled():
        # Флаг проверяется ДО валидации запроса: выключенный модуль не должен
        # даже отвечать по-разному на разные q — иначе он всё-таки существует.
        return {"enabled": False}
    query = (q or "").strip()
    if len(query) < MIN_QUERY_CHARS:
        raise HTTPException(400, {"message": f"запрос короче {MIN_QUERY_CHARS} символов",
                                  "hint": "пустой запрос вернул бы всю базу, а не результат"})
    if len(query) > MAX_QUERY_CHARS:
        raise HTTPException(400, {"message": f"запрос длиннее {MAX_QUERY_CHARS} символов"})
    return await search_all(request.app.state.svc, query, per_source=limit, total=total)


FEATURE = Feature(name="unified_search", router=router)
