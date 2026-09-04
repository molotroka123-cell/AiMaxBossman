"""web_research: ручки владельца — единственный способ увидеть, проверить и стереть.

Здесь нет ни одной новой возможности для модели. Всё, что делает этот файл,
делается ДЛЯ ЧЕЛОВЕКА и его же руками: показать, что уходило наружу, показать,
на чём держится каждая цитата, перепроверить её живой сетью и удалить эпизод
целиком. Ровно поэтому фича вообще имеет право существовать: собранное,
которое нельзя ни посмотреть, ни стереть, — это не «слой происхождения», а
чужой архив на диске владельца.

Чего этот файл НЕ делает и делать не должен:

  * **не заводит второго конвейера.** Поиск идёт через `sources.run_search`
    (внутри — чужой `osiris.collect`), чтение страницы — через `net.fetch_page`.
    «Тот же конвейер без модели» означает буквально тот же вызов, а не
    повторение его порядка проверок своими словами: вторая копия порядка
    однажды отстанет от первой, и отстанет молча;
  * **не рисует текст для модели.** Ручки отдают JSON. Всё, что модель читает
    глазами, собирает `render.py`, и он сюда не импортируется — иначе один и
    тот же факт печатался бы двумя разными способами;
  * **не удаляет доказательств ради места.** `prune_raw` трогает ТОЛЬКО сырьё
    источников с id на `web-`, на которое не ссылается ни одно наблюдение и ни
    одна запись индекса. Переполнение дискового бюджета останавливает СБОР
    (`net._check_disk_budget`), а не уборку улик;
  * **не трогает секретов вовсе и не заводит для них своего хранилища.**
    Ключей поисковых API этой сборке хранить негде, поэтому источник с
    `auth_mode="api_key"` виден в реестре с причиной «ключ не задан» и никогда
    не опрашивается молча — ровно так же, как у `tools.py`, чтобы готовность
    была ОДНА на владельца и на модель (см. `KEY_STORE_NOTE`);
  * **не создаёт файлов при выключенном флаге.** Читающие ручки отвечают
    `{"enabled": false, …}` и не касаются диска вовсе, мутирующие отдают 409 с
    указанием ИМЕННО того, чего не хватает.

Три места, где сделано не самое очевидное, и каждое по причине:

1. **Удаление эпизода сначала считает общее сырьё (поправка D1).**
   `OsirisStore.delete_subject` сносит файл сырья, если он подписан этим
   субъектом ИЛИ перечислен в его записи индекса. Ключ сырья у OSIRIS —
   `sha256(source_id|url)`, то есть один адрес, прочитанный в двух эпизодах, —
   это ОДИН файл, подписанный первым. Удаление второго эпизода молча выбило бы
   доказательство из-под первого. Поэтому пересечение считается ДО удаления и
   отдаётся ответом 409 со списком пострадавших; продолжить можно только явным
   `?orphan_ok=1` — то есть владелец, а не код, решает, чем пожертвовать.

2. **Перепроверка ВСЕГДА идёт в сеть (поправка E5).** `force=True` и никакого
   отката к кэшу: «цитата цела» из архива — это отчёт о непроведённой проверке.
   Сеть не ответила — статус `unreachable`, и он НЕ равен `gone`. Разница между
   «страницы больше нет» и «до сети не дошли» — это разница между «улика
   уничтожена» и «мы её не смотрели», и склеивать их нельзя.

3. **Показ цитаты извлекает страницу ТЕМИ ЖЕ параметрами (поправка D6).**
   Смещение цитаты записано В ИЗВЛЕЧЁННЫЙ текст, а он зависит от `max_chars` и
   от версии извлекателя. Извлечь «как сейчас удобно» значит подсветить не ту
   фразу и назвать это доказательством. Параметры берутся из паспорта самой
   цитаты, а расхождение версии извлекателя говорится вслух отдельным полем, а
   не прячется.

Честный предел, названный вслух: тела страниц и заголовки выдачи — текст
ВНЕШНЕГО происхождения. Ручки отдают его владельцу как есть (это улика, и
править улику нельзя), помечая ответ полем `external_untrusted`. Чистка через
`render.safe` применяется там, где текст читает МОДЕЛЬ, а не здесь.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping

from fastapi import APIRouter, HTTPException, Query, Request

from ... import html_text
from ...db import utcnow
from ...plugin_security import PluginSecurityError
from .. import osiris
from . import config, gate, ledger, net, sources, tools

router = APIRouter()

# Потолки ответов. Не «оптимизация»: ручка владельца обязана отвечать и на
# хранилище, которое росло полгода, а не падать по памяти на самом интересном.
MAX_EPISODES = 200
MAX_TRAIL_NODES = 500
TRAIL_MAX_DEPTH = 8
RAW_BODY_MAX_CHARS = 200_000
SEARCH_LIMIT_MAX = 10

# Формы идентификаторов. Проверяются ДО любого обращения к диску: `obs_id` и
# `digest` приходят из адреса запроса, а `subject_key` — из index.json, то есть
# из файла, который владелец мог править руками. Ни один из них не имеет права
# стать частью пути без проверки.
OBS_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6,12}-[0-9a-f]{4,32}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
SUBJECT_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")

# Наблюдения, которые делает ЭТА фича. По ним эпизод узнаётся как веб-эпизод, а
# не по имени субъекта: субъект — это текст запроса владельца, и подстроки в
# нём ничего не доказывают.
WEB_ATTRS = frozenset({"search.query", "search.result", "page.text", "quote",
                       "citation.check"})

# Исходы перепроверки. `superseded` в этом файле НЕ выдаётся никогда: он
# означает «есть более новое наблюдение вместо этого», а такое решение
# принимает тот, кто выпускает новую цитату, а не тот, кто проверяет старую.
# Обещать статус, который код не ставит, значит писать документацию вместо
# реализации.
RECHECK_STATUSES = ("intact", "moved", "changed", "gone", "unreachable", "blocked")

# Почему `api_keys` не передаётся НИКУДА из этого файла, и это не забывчивость.
# Единственное зашифрованное хранилище процесса (`svc.vault`) держит ключи
# ПРОВАЙДЕРОВ МОДЕЛЕЙ, а не ключи поисковых API; своего хранилища секретов фича
# не заводит (второе такое место — это второе место, откуда секрет утекает).
# Заявить «ключ есть» без установленного `net.KeyedFetchAdapter` значило бы
# получить тихий 401 и показать его владельцу как «источник сломался».
# И главное: `tools.py` тоже не передаёт ключей, а готовность обязана быть ОДНА
# на владельца и на модель — иначе ручка говорит «общий веб-поиск доступен», а
# модель в том же прогоне отвечает «искать негде». Поэтому `brave-search` виден
# в реестре с причиной «ключ не задан» и НИКОГДА не опрашивается молча.
KEY_STORE_NOTE = ("хранилища ключей поисковых API в этой сборке нет: источник с "
                  "auth_mode=api_key виден в реестре и не опрашивается. Общий "
                  "веб-поиск включается своим SearXNG (BOSSMAN_WEB_SEARXNG_URL)")


# --------------------------------------------------------------- служебное


def _svc(request: Request) -> Any:
    return request.app.state.svc


def _fail(exc: Exception) -> HTTPException:
    """Одна таблица перевода отказов в HTTP на все ручки сразу.

    `PluginSecurityError` отдаётся ОТДЕЛЬНЫМ кодом `egress_blocked`, а не
    растворяется в общем 502: «источник сломался» и «нам туда нельзя» — разные
    события, и владельцу, читающему ответ, важно именно второе.
    """
    if isinstance(exc, PluginSecurityError):
        return HTTPException(403, {"message": f"адрес отклонён проверкой egress: {exc}",
                                   "code": "egress_blocked"})
    if isinstance(exc, osiris.OsirisError):
        # Код и статус живут на самом исключении (osiris и net.PageRefused), а
        # не выводятся из текста: разбор текста ошибки регулярками — это способ
        # однажды перепутать 403 с 502 после безобидной правки формулировки.
        return HTTPException(int(getattr(exc, "http_status", 400)),
                             {"message": str(exc),
                              "code": str(getattr(exc, "code", "osiris_error"))})
    return HTTPException(500, {"message": f"{exc.__class__.__name__}: {exc}",
                               "code": "internal_error"})


async def _emit(svc: Any, kind: str, **data: Any) -> None:
    bus = getattr(svc, "bus", None)
    if bus is not None:
        await bus.emit(kind, **data)


def _store(svc: Any) -> osiris.OsirisStore:
    return osiris.store(svc)


def _index_subjects(st: osiris.OsirisStore) -> list[tuple[str, str, dict]]:
    """(ключ каталога, субъект, запись индекса). Кривые записи пропускаются.

    Ключ проверяется регуляркой, потому что index.json — файл на диске
    владельца: строка оттуда попадает в путь, и `..` в ней означала бы чтение
    чужого каталога руками моего кода.
    """
    index = st.index()
    rows: list[tuple[str, str, dict]] = []
    subjects = index.get("subjects") if isinstance(index, dict) else None
    for key, entry in (subjects or {}).items():
        name = str(key)
        if not isinstance(entry, dict) or not SUBJECT_KEY_RE.match(name):
            continue
        subject = str(entry.get("subject") or "")
        if not subject:
            continue
        rows.append((name, subject, entry))
    return rows


def _obs_rows(st: osiris.OsirisStore, subject: str) -> list[dict]:
    return [r for r in st.observations(subject) if isinstance(r, dict)]


def _is_web_row(row: Mapping[str, Any]) -> bool:
    """Наблюдение сделано этой фичей? По атрибуту и по источнику, а не по
    субъекту: субъект — это текст владельца, и он ничего не доказывает."""
    if str(row.get("attribute") or "") in WEB_ATTRS:
        return True
    source_id = str(row.get("source_id") or "")
    return source_id.startswith("web-") or source_id in sources.BACKENDS_BY_ID


def _parse_iso(value: Any) -> datetime | None:
    """Время из строки паспорта или None. Своя строчка вместо приватного
    `net._parse_iso`: приватное имя чужого модуля имеет полное право
    поменяться, а разбор ISO — не та сложность, ради которой стоит на него
    опираться."""
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _digest_of(raw_ref: Any) -> str:
    text = str(raw_ref or "")
    return text[4:] if text.startswith("raw:") else ""


def _raw_owners(st: osiris.OsirisStore) -> dict[str, set[str]]:
    """Дайджест сырья → субъекты, которые на него ссылаются.

    Считается по ТРЁМ источникам сразу, и это не перестраховка: удаление
    смотрит ровно на них. `delete_subject` сносит файл, если он подписан
    субъектом (`record["subject"]`) ИЛИ перечислен в записи индекса; а
    настоящая ссылка на улику живёт в `raw_ref` наблюдения, и она главнее
    обоих, потому что именно её печатает цитата.
    """
    owners: dict[str, set[str]] = {}

    def add(digest: str, subject: str) -> None:
        if digest and subject:
            owners.setdefault(digest, set()).add(subject)

    for _key, subject, entry in _index_subjects(st):
        for digest in entry.get("raw") or ():
            add(str(digest), subject)
    if st.obs_dir.is_dir():
        for path in sorted(st.obs_dir.glob("*/*.json")):
            row = config.read_json(path)
            if isinstance(row, dict):
                add(_digest_of(row.get("raw_ref")), str(row.get("subject") or ""))
    if st.raw_dir.is_dir():
        for path in sorted(st.raw_dir.glob("*.json")):
            record = config.read_json(path)
            if isinstance(record, dict):
                add(str(record.get("hash") or path.stem), str(record.get("subject") or ""))
    return owners


def _referenced_digests(st: osiris.OsirisStore) -> set[str]:
    """Сырьё, на которое ссылается хоть что-нибудь. Уборка трогает только то,
    чего здесь нет."""
    out: set[str] = set()
    for _key, _subject, entry in _index_subjects(st):
        out |= {str(d) for d in (entry.get("raw") or ())}
    if st.obs_dir.is_dir():
        for path in sorted(st.obs_dir.glob("*/*.json")):
            row = config.read_json(path)
            if isinstance(row, dict):
                digest = _digest_of(row.get("raw_ref"))
                if digest:
                    out.add(digest)
    return out


def prune_raw(svc: Any) -> dict[str, Any]:
    """Единственный уборщик сырья. Зовётся из `tick()` и из ручки удаления.

    Три условия, и каждое обязательно:

      * трогаем только источники с id на `web-` — сырьё собственных сборов
        OSIRIS не наше, и убирать чужое «заодно» нельзя;
      * трогаем только то, на что не ссылается ни одно наблюдение и ни одна
        запись индекса;
      * битый или нечитаемый файл НЕ удаляется. Он мог быть уликой, а
        «не разобрался» — не основание её уничтожить (fail-closed именно в эту
        сторону).

    Референсное сырьё не удаляется никогда, даже при переполнении дискового
    бюджета: при переполнении система отказывается СОБИРАТЬ новое
    (`net._check_disk_budget`), а не выбрасывает доказательство ради места.
    """
    st = _store(svc)
    used_before, limit = net.raw_budget_state(st)
    report = {"scanned": 0, "removed": 0, "kept": 0, "freed_bytes": 0,
              "pointers_removed": 0, "used_bytes": used_before, "limit_bytes": limit}
    if not st.raw_dir.is_dir():
        return report

    referenced = _referenced_digests(st)
    for path in sorted(st.raw_dir.glob("*.json")):
        record = config.read_json(path)
        if not isinstance(record, dict):
            continue
        if not str(record.get("source_id") or "").startswith("web-"):
            continue
        report["scanned"] += 1
        digest = str(record.get("hash") or path.stem)
        if digest in referenced or path.stem in referenced:
            report["kept"] += 1
            continue
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            report["kept"] += 1
            continue
        report["removed"] += 1
        report["freed_bytes"] += size
    report["pointers_removed"] = net.prune_pointers(svc)
    report["used_bytes"] = net.raw_bytes_used(st)
    return report


def _shared_raw(st: osiris.OsirisStore, subject: str) -> list[dict[str, Any]]:
    """Сырьё эпизода, которое делят с ним другие эпизоды (поправка D1)."""
    owners = _raw_owners(st)
    shared: list[dict[str, Any]] = []
    for digest, subjects in sorted(owners.items()):
        if subject not in subjects:
            continue
        others = sorted(subjects - {subject})
        if others:
            shared.append({"digest": digest, "subjects": others})
    return shared


# ------------------------------------------------- шлюз исходящего запроса


# Шлюза запроса здесь СВОЕГО НЕТ намеренно. Правило «наружу уходит только то,
# что выглядит как фраза человека» (поправка A3) реализовано ровно один раз — в
# `tools.guard_query`, — и эта ручка зовёт ту же функцию. Вторая реализация
# одного правила означала бы, что владелец, проверяющий поиск руками, и модель,
# ищущая в прогоне, живут по РАЗНЫМ правилам; разойдутся они не в первый месяц,
# так в третий, и заметит это только тот, кто ищет обход.


# ------------------------------------------------------------ состояние


def _flags() -> dict[str, Any]:
    return {"web": config.FLAG, "osiris": config.OSIRIS_FLAG,
            "web_enabled": config.enabled(), "osiris_enabled": osiris.enabled()}


@router.get("/web")
async def web_state(request: Request):
    """Состояние фичи целиком: флаги, готовность, счётчики, бюджеты, преполёт.

    При выключенном флаге не читается ни один файл: готовность считается по
    константам и по списку backend'ов, а `counts`, бюджеты и преполёт остаются
    пустыми. Выключенная фича обязана вести себя ровно как её отсутствие, а
    «мы только посмотрели» — это тоже поведение.
    """
    svc = _svc(request)
    ready = sources.readiness(svc)
    payload: dict[str, Any] = {
        "enabled": config.enabled(),
        "flags": _flags(),
        "readiness": ready,
        "config": config.as_dict(),
        "counts": None,
        "budget": None,
        "preflight": None,
        "external_untrusted": True,
    }
    if not config.both_enabled():
        return payload

    st = _store(svc)
    used, limit = net.raw_budget_state(st)
    payload["counts"] = st.counts()
    payload["budget"] = {
        "daily": ledger.daily_state(svc),
        "raw": {"used_bytes": used, "limit_bytes": limit,
                "used_mb": round(used / 1_000_000, 1),
                "limit_mb": round(limit / 1_000_000, 1),
                "full": used >= limit},
        "per_run": {"searches": config.MAX_SEARCHES_PER_RUN,
                    "opens": config.MAX_OPENS_PER_RUN,
                    "bytes": config.MAX_RUN_BYTES,
                    "net_seconds": config.MAX_RUN_NET_SECONDS},
    }
    # Файл преполёта пишет `gate.py`, он же его и читает: путь к нему —
    # его дело, а не наше. Отсутствие файла означает «не проверялось».
    payload["preflight"] = gate.last_preflight(svc)
    return payload


@router.get("/web/sources")
async def web_sources(request: Request):
    """Реестр backend'ов и автосозданных источников-на-хост.

    `key_store` говорит владельцу прямо, что класть ключ поискового API некуда:
    знать это лучше, чем искать несуществующее поле в интерфейсе. Обещание
    «добавьте ключ Brave» без места, куда его добавить, — это документация
    вместо работающего пути.
    """
    svc = _svc(request)
    payload: dict[str, Any] = {
        "enabled": config.enabled(),
        "flags": _flags(),
        "backends": sources.backend_status(svc),
        "key_store": KEY_STORE_NOTE,
        "declaration_problems": dict(sources.DECL_PROBLEMS),
        "tos_checked_at": sources.TOS_CHECKED_AT,
        "parsers": list(sources.PARSER_NAMES),
        "host_sources": [],
        "forbidden_serp": [{"pattern": p.pattern, "why": w} for p, w in config.SERP_DENY],
        "exfil_sinks": sorted(config.EXFIL_SINKS),
    }
    if not config.both_enabled():
        return payload
    payload["host_sources"] = [
        src.as_dict() for src in _store(svc).sources().values()
        if src.id.startswith("web-")]
    return payload


# ------------------------------------------------------------- эпизоды


def _episode_summary(subject: str, rows: list[dict]) -> dict[str, Any]:
    """Свод по эпизоду. Цифры считаются по наблюдениям, а не берутся из
    индекса: индекс — это ускоритель, а истина лежит в паспортах."""
    by_attr: dict[str, int] = {}
    hosts: set[str] = set()
    backends: set[str] = set()
    digests: set[str] = set()
    stamps: list[str] = []
    transports: set[str] = set()
    for row in rows:
        attr = str(row.get("attribute") or "")
        by_attr[attr] = by_attr.get(attr, 0) + 1
        source_id = str(row.get("source_id") or "")
        if source_id in sources.BACKENDS_BY_ID:
            backends.add(source_id)
        value = row.get("value") if isinstance(row.get("value"), dict) else {}
        host = str(value.get("host") or "")
        if host:
            hosts.add(host)
        transport = str(value.get("transport") or "")
        if transport:
            transports.add(transport)
        digest = _digest_of(row.get("raw_ref"))
        if digest:
            digests.add(digest)
        stamps.append(str(row.get("observed_at") or ""))
    stamps = sorted(s for s in stamps if s)
    return {
        "subject": subject,
        "query": sources.query_of(subject),
        "observations": len(rows),
        "by_attribute": by_attr,
        "backends": sorted(backends),
        "hosts": sorted(hosts),
        "raw_digests": sorted(digests),
        # D5: стенд не отмывается в след как настоящая сеть — эпизод, где хоть
        # одно наблюдение сделано подменённым транспортом, помечен здесь.
        "transports": sorted(transports),
        "first_observed_at": stamps[0] if stamps else "",
        "last_observed_at": stamps[-1] if stamps else "",
    }


@router.get("/web/episodes")
async def web_episodes(request: Request,
                       limit: int = Query(MAX_EPISODES, ge=1, le=MAX_EPISODES),
                       with_runs: int = Query(0, ge=0, le=1),
                       everything: int = Query(0, ge=0, le=1)):
    """Эпизоды: один субъект OSIRIS = один эпизод.

    `with_runs=1` доклеивает номера прогонов, в реестрах которых встречается
    эпизод. По умолчанию выключено намеренно: этот поиск перебирает файлы
    реестров для КАЖДОГО эпизода, и платить за него в обычном списке незачем —
    нужен он только перед удалением.
    """
    svc = _svc(request)
    if not config.both_enabled():
        return {"enabled": config.enabled(), "flags": _flags(), "episodes": [],
                "total": 0, "external_untrusted": True}
    st = _store(svc)
    episodes: list[dict[str, Any]] = []
    for _key, subject, _entry in _index_subjects(st):
        rows = _obs_rows(st, subject)
        if not everything and not any(_is_web_row(r) for r in rows):
            continue
        episodes.append(_episode_summary(subject, rows))
    total = len(episodes)
    # Сортировка ДО обрезки, а не после: «первые сто» обязаны означать сто
    # самых свежих, а не сто первых попавшихся, отсортированных между собой.
    episodes.sort(key=lambda e: e["last_observed_at"], reverse=True)
    episodes = episodes[:limit]
    if with_runs:
        for episode in episodes:
            episode["runs"] = ledger.Ledger.subject_runs(svc, episode["subject"])
    return {"enabled": True, "flags": _flags(), "episodes": episodes,
            "total": total, "shown": len(episodes), "external_untrusted": True}


# ---------------------------------------------------------------- след


def _node(row: Mapping[str, Any]) -> dict[str, Any]:
    """Узел следа: паспорт целиком плюс короткая выжимка для глаз."""
    value = row.get("value") if isinstance(row.get("value"), dict) else {}
    attr = str(row.get("attribute") or "")
    summary: dict[str, Any] = {}
    if attr == "search.query":
        summary = {k: value.get(k) for k in
                   ("query", "backend", "outcome", "detail", "results", "dropped")}
    elif attr == "search.result":
        summary = {k: value.get(k) for k in ("rank", "title", "host", "trusted")}
    elif attr == "page.text":
        summary = {k: value.get(k) for k in
                   ("url", "chars", "title", "transport", "from_cache", "truncated",
                    "quotable", "hidden_dropped")}
    elif attr == "quote":
        summary = {k: value.get(k) for k in ("quote", "claim", "offset", "length")}
    elif attr == "citation.check":
        summary = {k: value.get(k) for k in ("status", "detail", "transport")}
    return {
        "id": str(row.get("id") or ""),
        "attribute": attr,
        "of": str(value.get("of") or ""),
        "source_id": str(row.get("source_id") or ""),
        "source_url": str(row.get("source_url") or ""),
        "observed_at": str(row.get("observed_at") or ""),
        "collected_at": str(row.get("collected_at") or ""),
        "confidence": row.get("confidence"),
        "raw_ref": row.get("raw_ref"),
        "summary": summary,
    }


def _tree(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Плоский список наблюдений → дерево по `value["of"]`.

    Ограничитель глубины стоит не от циклов (идентификатор наблюдения выводится
    из его содержимого, и сын не может оказаться отцом), а от файла, правленого
    руками: дерево строится по данным с диска, и рекурсия по ним обязана иметь
    дно.
    """
    by_id = {n["id"]: n for n in nodes if n["id"]}
    children: dict[str, list[dict[str, Any]]] = {}
    roots: list[dict[str, Any]] = []
    for node in nodes:
        parent = node["of"]
        if parent and parent in by_id and parent != node["id"]:
            children.setdefault(parent, []).append(node)
        else:
            roots.append(node)

    def build(node: dict[str, Any], depth: int) -> dict[str, Any]:
        out = dict(node)
        out["children"] = ([] if depth >= TRAIL_MAX_DEPTH
                           else [build(c, depth + 1) for c in children.get(node["id"], ())])
        return out

    return [build(r, 0) for r in roots]


@router.get("/web/trail")
async def web_trail(request: Request, subject: str = Query(""), url: str = Query("")):
    """След эпизода: запрос → выдача → страница → цитата → перепроверка.

    Искать можно по субъекту или по адресу. По адресу — потому что владелец
    помнит, ЧТО прочитала модель, а не под каким субъектом это легло; адрес
    приводится к канонической форме той же функцией, что и везде, иначе
    `example.com/A` и `example.com/a/` считались бы разными следами.
    """
    svc = _svc(request)
    if not config.both_enabled():
        return {"enabled": config.enabled(), "flags": _flags(), "subjects": [],
                "nodes": [], "tree": [], "external_untrusted": True}
    wanted_url = ""
    if url.strip():
        try:
            wanted_url = html_text.canon_url(url)
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(400, {"message": f"адрес не канонизуется: {exc}",
                                      "code": "bad_url"}) from exc
    if not subject.strip() and not wanted_url:
        raise HTTPException(400, {"message": "нужен ?subject= или ?url=",
                                  "code": "no_selector"})

    st = _store(svc)
    picked: list[tuple[str, list[dict]]] = []
    if subject.strip():
        rows = _obs_rows(st, subject.strip())
        if rows:
            picked.append((subject.strip(), rows))
    else:
        for _key, name, _entry in _index_subjects(st):
            rows = _obs_rows(st, name)
            hit = any(
                isinstance(r.get("value"), dict)
                and wanted_url in (str(r["value"].get("url") or ""),
                                   str(r["value"].get("requested_url") or ""))
                for r in rows)
            if hit:
                picked.append((name, rows))

    nodes: list[dict[str, Any]] = []
    for _name, rows in picked:
        for row in rows:
            nodes.append(_node(row))
            if len(nodes) >= MAX_TRAIL_NODES:
                break
        if len(nodes) >= MAX_TRAIL_NODES:
            break
    # Порядок — по времени СБОРА и по возрастанию: след читают сверху вниз, как
    # историю, а `observations()` отдаёт от новых к старым.
    nodes.sort(key=lambda n: (n["collected_at"], n["id"]))
    return {"enabled": True, "flags": _flags(),
            "subjects": [name for name, _rows in picked],
            "url": wanted_url, "nodes": nodes, "tree": _tree(nodes),
            "truncated": len(nodes) >= MAX_TRAIL_NODES,
            "external_untrusted": True}


# --------------------------------------------------------------- цитата


def _find_observation(st: osiris.OsirisStore,
                      obs_id: str) -> tuple[str, str, dict] | None:
    """(субъект, ключ каталога, наблюдение) или None.

    Идентификатор проверен регуляркой вызывающим, ключ каталога — в
    `_index_subjects`: обе строки попадают в путь, и обе приходят снаружи.
    """
    for key, subject, entry in _index_subjects(st):
        if obs_id not in {str(x) for x in (entry.get("observations") or ())}:
            continue
        row = config.read_json(st.obs_dir / key / f"{obs_id}.json")
        if isinstance(row, dict):
            return subject, key, row
    return None


def _extract_same_way(record: Mapping[str, Any],
                      value: Mapping[str, Any]) -> tuple[Any, int, str, bool]:
    """Извлечь страницу ТЕМИ ЖЕ параметрами, что и в момент цитирования (D6).

    Возвращает (извлечение, max_chars, версия извлекателя из паспорта, совпала
    ли версия). Смещение цитаты записано В ИЗВЛЕЧЁННЫЙ текст: другой
    `max_chars` — другой текст и другое смещение, поэтому «извлечём как сейчас
    удобно» означает подсветить не ту фразу и назвать это доказательством.

    Расхождение версии извлекателя НЕ чинится подбором и не скрывается: оно
    отдаётся отдельным полем, потому что подсветка по чужой версии — это
    именно то, что владельцу нужно уметь отличить.
    """
    try:
        max_chars = int(value.get("max_chars") or record.get("extract_max_chars") or 0)
    except (TypeError, ValueError):
        max_chars = 0
    if max_chars <= 0:
        max_chars = net.EXTRACT_MAX_CHARS
    declared = str(value.get("extractor") or value.get("extractor_version")
                   or record.get("extractor") or "")
    extraction = html_text.extract(str(record.get("body") or ""),
                                   base_url=str(record.get("url") or ""),
                                   max_chars=max_chars)
    return extraction, max_chars, declared, (not declared
                                             or declared == html_text.EXTRACTOR_VERSION)


@router.get("/web/citations/{obs_id}")
async def web_citation(obs_id: str, request: Request,
                       context: int = Query(400, ge=0, le=4000)):
    """Цитата вместе с доказательством: паспорт, сырьё, точное место в тексте.

    Проверка делается ЗДЕСЬ И СЕЙЧАС по сохранённому сырью, а не берётся из
    записи: запись говорит, что цитата была дословной в момент выдачи, а
    владелец спрашивает, дословна ли она сейчас в том, что лежит на диске.
    Ответ «сместилась» честнее ответа «верна», и он же — единственный способ
    заметить, что извлекатель или потолок знаков поменялись под ногами.
    """
    svc = _svc(request)
    if not config.both_enabled():
        return {"enabled": config.enabled(), "flags": _flags(), "observation": None,
                "verification": {"status": "disabled",
                                 "why": "фича выключена: на диске ничего не читается"},
                "external_untrusted": True}
    if not OBS_ID_RE.match(obs_id.strip()):
        raise HTTPException(400, {"message": "негодный идентификатор наблюдения",
                                  "code": "bad_observation_id"})
    st = _store(svc)
    found = _find_observation(st, obs_id.strip())
    if found is None:
        raise HTTPException(404, {"message": "наблюдение не найдено",
                                  "code": "observation_unknown"})
    subject, _key, row = found
    value = row.get("value") if isinstance(row.get("value"), dict) else {}
    digest = _digest_of(row.get("raw_ref"))
    record = st.read_raw(digest) if digest else None

    out: dict[str, Any] = {
        "enabled": True, "subject": subject, "observation": row,
        "raw_digest": digest, "raw_present": isinstance(record, dict),
        "verification": {"status": "no_raw",
                         "why": "сырьё, на которое ссылается цитата, не найдено"},
        "external_untrusted": True,
    }
    if not isinstance(record, dict):
        return out

    extraction, max_chars, declared, same_extractor = _extract_same_way(record, value)
    out["extraction"] = {"max_chars": max_chars, "extractor_in_passport": declared,
                         "extractor_now": html_text.EXTRACTOR_VERSION,
                         "extractor_matches": same_extractor,
                         "chars": extraction.chars, "truncated": extraction.truncated}
    # D5: транспорт записан в сырьё; цитата из стенда не выдаётся за сетевую.
    out["transport"] = str(record.get("transport") or "")
    out["fetched_at"] = str(record.get("fetched_at") or "")

    quote = str(value.get("quote") or "")
    if not quote:
        out["verification"] = {"status": "not_a_quote",
                               "why": "у этого наблюдения нет поля quote"}
        return out

    try:
        offset = int(value.get("offset") or 0)
    except (TypeError, ValueError):
        offset = 0
    length = len(quote)
    exact = extraction.text[offset:offset + length] == quote if offset >= 0 else False
    if exact:
        status, found_at = "exact", offset
    else:
        hit = html_text.find_quote(extraction, quote)
        status, found_at = ("moved", hit[0]) if hit else ("not_found", -1)
    block = html_text.block_at(extraction, found_at) if found_at >= 0 else None
    out["verification"] = {
        "status": status,
        "offset_in_passport": offset,
        "offset_now": found_at,
        "length": length,
        "block_index": block.index if block is not None else 0,
        "why": {"exact": "цитата на том же месте",
                "moved": "цитата в тексте есть, но смещение изменилось",
                "not_found": "такой цитаты в сохранённом тексте нет"}[status],
    }
    if found_at >= 0 and context:
        start = max(0, found_at - context)
        out["context"] = {"before": extraction.text[start:found_at],
                          "quote": extraction.text[found_at:found_at + length],
                          "after": extraction.text[found_at + length:
                                                   found_at + length + context]}
    return out


# ----------------------------------------------------------------- сырьё


@router.get("/web/raw/{digest}")
async def web_raw(digest: str, request: Request,
                  body: int = Query(0, ge=0, le=1),
                  refs: int = Query(0, ge=0, le=1)):
    """Сохранённое сырьё — то самое, на что ссылается цитата.

    Тело отдаётся КАК ЕСТЬ и только по явному `?body=1`. Как есть — потому что
    это улика: обезвреженное тело уже не доказывает того, что было на странице.
    По явному запросу — потому что 400 килобайт чужого текста в ответе на
    «покажи метаданные» никому не нужны.
    """
    svc = _svc(request)
    if not config.both_enabled():
        return {"enabled": config.enabled(), "flags": _flags(), "digest": "",
                "meta": None, "external_untrusted": True}
    key = digest.strip().lower()
    if not DIGEST_RE.match(key):
        raise HTTPException(400, {"message": "дайджест сырья — 64 шестнадцатеричных знака",
                                  "code": "bad_digest"})
    st = _store(svc)
    record = st.read_raw(key)
    if not isinstance(record, dict):
        raise HTTPException(404, {"message": "сырья с таким дайджестом нет",
                                  "code": "raw_unknown"})
    text = str(record.get("body") or "")
    out: dict[str, Any] = {
        "enabled": True,
        "digest": key,
        "meta": {k: v for k, v in record.items() if k != "body"},
        "fresh": st.raw_is_fresh(record),
        "body_chars": len(text),
        "body_included": bool(body),
        "body_truncated": False,
        "external_untrusted": True,
    }
    if body:
        out["body"] = text[:RAW_BODY_MAX_CHARS]
        out["body_truncated"] = len(text) > RAW_BODY_MAX_CHARS
    if refs:
        owners = _raw_owners(st)
        out["referenced_by_subjects"] = sorted(owners.get(key, set()))
    return out


# ---------------------------------------------------------------- реестр


@router.get("/web/ledger/{run_id}")
async def web_ledger(run_id: str, request: Request):
    """Реестр ссылок прогона: что стоит за каждым токеном и сколько бюджета цело.

    Отсутствующий реестр — это `exists: false`, а не 404: прогон, не ходивший в
    сеть, файла не создаёт, и «нет файла» — нормальный ответ, а не ошибка.
    """
    svc = _svc(request)
    if not config.both_enabled():
        return {"enabled": config.enabled(), "flags": _flags(), "exists": False,
                "ledger": None, "external_untrusted": True}
    try:
        path = ledger.Ledger.path_for(svc, run_id)
    except ValueError as exc:
        raise HTTPException(400, {"message": str(exc), "code": "bad_run_id"}) from exc
    exists = path.exists()
    # `load` не создаёт файла и ничего не пишет для целого реестра. Испорченный
    # он отодвигает в `.broken-<время>` — это запись, но запись об улике: файл
    # не затирается, а сохраняется под другим именем, и молчаливо читать его
    # как «просто пустой» было бы хуже.
    led = ledger.Ledger.load(svc, run_id)
    return {"enabled": True, "flags": _flags(), "exists": exists,
            "ledger": led.as_dict(), "budget": led.left(),
            "external_untrusted": True}


@router.delete("/web/ledger/{run_id}")
async def web_ledger_delete(run_id: str, request: Request):
    """Удалить реестр прогона. Идемпотентно: повторный вызов — не ошибка."""
    svc = _svc(request)
    config._require_enabled()                                  # noqa: SLF001
    try:
        ledger.Ledger.path_for(svc, run_id)
    except ValueError as exc:
        raise HTTPException(400, {"message": str(exc), "code": "bad_run_id"}) from exc
    removed = ledger.Ledger.delete(svc, run_id)
    await _emit(svc, "web.ledger_deleted", run_id=str(run_id), removed=removed)
    return {"ok": True, "run_id": str(run_id), "removed": removed}


# ----------------------------------------------------------------- поиск


@router.post("/web/search")
async def web_search(request: Request, body: dict):
    """Тот же конвейер поиска, но без модели: владелец проверяет сам.

    «Тот же» — буквально: выбор backend'а и сбор делает `sources`, порядок
    проверок внутри — чужой `osiris.collect`. Здесь добавлены ровно две вещи,
    которых у инструмента нет и быть не может: шлюз запроса на своём месте
    (наружу уходят байты с машины владельца, и правило одно для всех) и списание
    СУТОЧНОГО лимита — он общий на машину, а не на прогон.

    Отказ отдаётся ДАННЫМИ с кодом исхода, а не исключением: «движки не
    ответили», «ничего не найдено» и «источник сломался» — три разных ответа
    (поправка E1), и склеивать их в один HTTP-код значит потерять именно то
    различение, ради которого поправка написана.
    """
    svc = _svc(request)
    config._require_enabled()                                  # noqa: SLF001
    query = str((body or {}).get("query") or "")
    site = str((body or {}).get("site") or "").strip()
    fresh = bool((body or {}).get("fresh"))
    try:
        limit = int((body or {}).get("limit") or SEARCH_LIMIT_MAX)
    except (TypeError, ValueError):
        limit = SEARCH_LIMIT_MAX
    limit = max(1, min(limit, SEARCH_LIMIT_MAX))

    why = tools.guard_query(query)
    if why:
        await _emit(svc, "web.query_refused", why=why, via="api")
        raise HTTPException(400, {"message": config.MSG_QUERY_REFUSED.format(why=why),
                                  "code": "query_refused", "why": why})

    subject = sources.search_subject(query)
    backend = sources.pick_backend(svc, query, site)
    if backend is None:
        # «Искать негде» и «ничего не найдено» — разные ответы, и это не
        # придирка: первый означает, что наружу никто не ходил.
        return {"ok": False, "code": "no_backends", "subject": subject,
                "readiness": sources.readiness(svc), "hits": [],
                "external_untrusted": True}

    # Суточный лимит — резерв ДО обращения: узнать заранее, попадём ли мы в кэш,
    # нельзя, а списать «задним числом» уже ушедшие байты невозможно. Перерасход
    # здесь идёт в сторону осторожности, и это сказано вслух.
    if not ledger.daily_take(svc, 1):
        state = ledger.daily_state(svc)
        raise HTTPException(429, {
            "message": config.MSG_BUDGET_DAILY.format(used=state["used"],
                                                      limit=state["limit"]),
            "code": "daily_budget", "daily": state})

    # A3: каждый исходящий запрос виден владельцу в живой ленте ДОСЛОВНО и
    # СРАЗУ, а не только постфактум в эпизодах. Событие уходит ДО сети: если
    # обращение зависнет, след всё равно уже есть.
    await _emit(svc, "web.query_sent", query=subject, backend=backend.id,
                site=site, fresh=fresh, via="api")

    try:
        result = await sources.run_search(svc, backend, subject, force_refresh=fresh)
    except PluginSecurityError as exc:
        raise _fail(exc) from exc
    except osiris.OsirisError as exc:
        raise _fail(exc) from exc

    hits = list(result.get("hits") or ())[:limit]
    out = dict(result)
    out["hits"] = hits
    out["shown"] = len(hits)
    out["limit"] = limit
    out["daily"] = ledger.daily_state(svc)
    out["external_untrusted"] = True
    if result.get("code") == "private_door":
        # Не заглушка и не «здесь будет»: этот backend опрашивается приватной
        # дверью (`net.searxng_fetch`), а его выдача не может нести паспорт
        # OSIRIS вовсе — конструктор `Observation` прогоняет `source_url` через
        # проверку egress, и адрес на 127.0.0.1 её не проходит. Отдавать
        # результат без паспорта эта ручка не станет: она существует ровно
        # затем, чтобы показывать происхождение.
        out["hint"] = ("свой SearXNG опрашивается инструментом через приватную дверь; "
                       "его выдача не получает паспорта OSIRIS, поэтому ручка "
                       "владельца её не выдаёт")
    return out


@router.post("/web/refs")
async def web_mint_ref(request: Request, body: dict):
    """Владелец чеканит ссылку в реестр прогона — сам, до и мимо модели.

    Это единственный путь, которым адрес попадает в реестр не из выдачи и не со
    страницы. Проверки те же, что у конвейера чтения (`net.precheck_target`), и
    зовутся ДО чеканки: токен на адрес, куда всё равно нельзя, — это обещание,
    которого код не выполнит.

    Префикс токена возвращается отдельным полем намеренно. После первого же
    открытия страницы реестр заражён, и даже адрес владельца получает `l`, то
    есть потребует одобрения при открытии. Владельцу лучше узнать об этом
    здесь, чем удивиться в предпросмотре.
    """
    svc = _svc(request)
    config._require_enabled()                                  # noqa: SLF001
    run_id = str((body or {}).get("run_id") or "").strip()
    url = str((body or {}).get("url") or "").strip()
    if not run_id or not url:
        raise HTTPException(400, {"message": "нужны run_id и url", "code": "bad_request"})
    refusal = net.precheck_target(url)
    if refusal is not None:
        code, reason = refusal
        raise HTTPException(403, {"message": reason, "code": code})
    try:
        led = ledger.Ledger.load(svc, run_id)
    except ValueError as exc:
        raise HTTPException(400, {"message": str(exc), "code": "bad_run_id"}) from exc

    token = led.mint(url, kind="owner", origin="owner:api",
                     subject=str((body or {}).get("subject") or ""),
                     title=str((body or {}).get("title") or ""))
    if not token:
        raise HTTPException(409, {
            "message": ("токен не отчеканен: адрес не помещается в самоописывающую форму "
                        "токена либо реестр прогона переполнен"),
            "code": "mint_refused", "budget": led.left()})
    entry = led.resolve(token)
    await _emit(svc, "web.ref_minted", run_id=run_id, ref=token,
                host=entry.host if entry is not None else "", via="api")
    return {"ok": True, "run_id": run_id, "ref": token,
            "prefix": token[:1], "needs_approval": token.startswith("l"),
            "tainted": led.tainted,
            "entry": entry.as_dict() if entry is not None else None,
            "budget": led.left()}


# ----------------------------------------------------------- перепроверка


def _recheck_failure(exc: Exception) -> tuple[str, str]:
    """Исключение конвейера → (статус перепроверки, объяснение).

    Разделение `gone` и `unreachable` — обязательное (поправка E5): первое
    означает «страницы больше нет», второе — «мы её не смотрели». Всё, что не
    опознано уверенно, становится `unreachable`: осторожный ответ здесь тот,
    который НЕ утверждает судьбу страницы.

    HTTP-код приходится доставать из текста сообщения: `SourceUnavailableError`
    статуса не несёт, а заводить ради этого своё исключение поверх чужой
    иерархии значило бы развести две иерархии ошибок. Промах разбора даёт
    `unreachable`, то есть ошибается в безопасную сторону.
    """
    if isinstance(exc, PluginSecurityError):
        return "blocked", f"адрес отклонён проверкой egress: {exc}"
    if isinstance(exc, osiris.RobotsDisallowError):
        return "blocked", str(exc)
    if isinstance(exc, osiris.ForbiddenSourceError):
        return "blocked", str(exc)
    if isinstance(exc, osiris.RateLimitedError):
        return "unreachable", f"лимит запросов: {exc}"
    if isinstance(exc, net.PageRefused):
        if exc.code == "empty_text":
            # Сервер ответил, но читаемого текста больше нет: это изменение
            # страницы (или капча на ней), а не её исчезновение.
            return "changed", str(exc)
        return "blocked", str(exc)
    if isinstance(exc, osiris.SourceUnavailableError):
        match = re.search(r"HTTP\s+(\d{3})", str(exc))
        if match and match.group(1) in ("404", "410"):
            return "gone", str(exc)
        return "unreachable", str(exc)
    if isinstance(exc, osiris.OsirisError):
        return "unreachable", str(exc)
    return "unreachable", f"{exc.__class__.__name__}: {exc}"


@router.post("/web/recheck")
async def web_recheck(request: Request, body: dict):
    """Перепроверить цитату или страницу ЖИВОЙ СЕТЬЮ (поправка E5).

    `force=True` всегда: перепроверка, ответившая «цела» из кэша, — это отчёт о
    непроведённой проверке. Сеть не ответила — `unreachable`, и это НЕ `intact`.

    Результат — НОВОЕ наблюдение `citation.check`. Старое не переписывается
    никогда: история проверок и есть ценность, а «поправленная» запись стирает
    ровно тот факт, что когда-то было иначе.

    Страница перечитывается с ТЕМИ ЖЕ параметрами извлечения, что записаны в
    паспорте цитаты (поправка D6), иначе смещение указывало бы мимо, и
    «сместилась» нельзя было бы отличить от «поменялся потолок знаков».
    """
    svc = _svc(request)
    config._require_enabled()                                  # noqa: SLF001
    raw_body = body or {}
    obs_id = str(raw_body.get("observation_id") or raw_body.get("obs_id") or "").strip()
    if not OBS_ID_RE.match(obs_id):
        raise HTTPException(400, {"message": "нужен observation_id проверяемого наблюдения",
                                  "code": "bad_observation_id"})
    st = _store(svc)
    found = _find_observation(st, obs_id)
    if found is None:
        raise HTTPException(404, {"message": "наблюдение не найдено",
                                  "code": "observation_unknown"})
    subject, _key, row = found
    attribute = str(row.get("attribute") or "")
    if attribute not in ("quote", "page.text"):
        raise HTTPException(400, {
            "message": f"перепроверять можно цитату или страницу, а не {attribute!r}",
            "code": "not_recheckable"})
    method = str(row.get("method") or "")
    if method not in osiris.METHODS:
        raise HTTPException(409, {"message": "в паспорте наблюдения негодный method",
                                  "code": "bad_passport"})

    value = row.get("value") if isinstance(row.get("value"), dict) else {}
    url = str(value.get("url") or value.get("requested_url")
              or row.get("source_url") or "")
    if not url:
        raise HTTPException(409, {"code": "no_url",
                                  "message": "в наблюдении нет адреса для проверки"})
    old_ref = str(row.get("raw_ref") or "")
    old_record = st.read_raw(_digest_of(old_ref)) or {}
    quote = str(value.get("quote") or "")
    try:
        old_offset = int(value.get("offset") or 0)
    except (TypeError, ValueError):
        old_offset = 0
    try:
        max_chars = int(value.get("max_chars") or old_record.get("extract_max_chars") or 0)
    except (TypeError, ValueError):
        max_chars = 0
    if max_chars <= 0:
        max_chars = net.EXTRACT_MAX_CHARS
    declared = str(value.get("extractor") or value.get("extractor_version")
                   or old_record.get("extractor") or "")

    page = None
    new_offset = -1
    try:
        page = await net.fetch_page(svc, url, subject,
                                    ensure_host_source=sources.ensure_host_source,
                                    force=True, extract_chars=max_chars, of=obs_id)
    except Exception as exc:                   # noqa: BLE001 — беда мира = статус, не 500
        status, detail = _recheck_failure(exc)
    else:
        if quote:
            hit = html_text.find_quote(page.extraction, quote)
            if hit is None:
                status, detail = "changed", "цитаты в новом тексте страницы нет"
            else:
                new_offset = hit[0]
                if new_offset == old_offset:
                    status, detail = "intact", "цитата на прежнем месте"
                else:
                    status, detail = "moved", "цитата на месте, но смещение изменилось"
        else:
            old_sha = str(value.get("text_sha256") or "")
            if old_sha and old_sha == page.text_sha256:
                status, detail = "intact", "извлечённый текст страницы не изменился"
            else:
                status, detail = "changed", "извлечённый текст страницы изменился"

    # raw_ref нового наблюдения — сырьё ПРОВЕРЯЕМОЙ цитаты: паспорт обязан
    # ссылаться на то, о чём говорит запись, а говорит она о старой улике.
    # Новое сырьё (когда сеть ответила) названо отдельным полем.
    raw_ref = old_ref or (f"raw:{page.raw_digest}" if page is not None else "")
    if not raw_ref:
        raise HTTPException(409, {
            "message": "перепроверять нечего: у наблюдения нет сырья, а сеть не ответила",
            "code": "no_raw"})

    live = bool(page is not None and page.transport == "live")
    check_value: dict[str, Any] = {
        "of": obs_id,
        "status": status,
        "detail": detail,
        "attribute_checked": attribute,
        "url": url,
        "forced": True,
        "old_raw_ref": old_ref,
        "new_raw_ref": f"raw:{page.raw_digest}" if page is not None else "",
        "old_text_sha256": str(value.get("text_sha256") or ""),
        "new_text_sha256": page.text_sha256 if page is not None else "",
        "offset_before": old_offset,
        "offset_after": new_offset,
        "extractor_in_passport": declared,
        "extractor_now": html_text.EXTRACTOR_VERSION,
        "extractor_matches": (not declared or declared == html_text.EXTRACTOR_VERSION),
        "max_chars": max_chars,
        # D5: перепроверка подменённым транспортом — это не проверка сетью, и
        # ответ обязан это говорить, а не выглядеть как настоящий.
        "transport": page.transport if page is not None else "",
        "verified_live": live,
        "checked_at": utcnow().isoformat(),
    }
    # Наблюдено ТОГДА, когда сеть отдала новое тело; когда не отдала — наблюдён
    # сам факт недоступности, и он наблюдён сейчас. Обе даты правдивы, и ни одна
    # не выдумана: подставить сюда время старого чтения значило бы сказать, что
    # мы проверяли тогда.
    observed_at = utcnow()
    if page is not None:
        observed_at = _parse_iso(page.fetched_at) or observed_at
    # Паспорт новой записи собирается из полей СТАРОЙ, а старая лежит в файле,
    # который владелец мог править руками. Неполный паспорт — это отказ
    # конструктора `Observation`, и он обязан стать понятным ответом, а не 500:
    # «у проверяемого наблюдения испорчен паспорт» — это диагноз, а не авария.
    try:
        observation = osiris.Observation(
            value=check_value, subject=subject,
            source_id=str(row.get("source_id") or ""),
            source_url=str(row.get("source_url") or url),
            method=method, license=str(row.get("license") or ""),
            observed_at=observed_at, collected_at=st.next_collected_at(),
            confidence=min(0.5, float(row.get("confidence") or 0.5)),
            raw_ref=raw_ref, attribute="citation.check")
    except (osiris.OsirisError, PluginSecurityError) as exc:
        raise _fail(exc) from exc
    st.save_observations(subject, [observation], [])
    await _emit(svc, "web.recheck", subject=subject, of=obs_id, status=status,
                transport=check_value["transport"])
    return {"ok": True, "status": status, "statuses": list(RECHECK_STATUSES),
            "subject": subject, "observation": observation.as_dict(),
            "checked": row, "external_untrusted": True}


# ------------------------------------------------------------- преполёт


@router.post("/web/preflight")
async def web_preflight(request: Request, body: dict | None = None):
    """Проверка раннера владельца: отдаёт ли модель нативные `tool_calls`.

    Сама проверка живёт в `gate.py` (там же, где хук, который лечит последствия
    ответа «нет»). Здесь только вызов: дублировать проверку возможностей модели
    в ручке значило бы получить два вердикта об одном и том же.
    """
    svc = _svc(request)
    config._require_enabled()                                  # noqa: SLF001
    model_id = (body or {}).get("model_id")
    try:
        return await gate.preflight(svc, model_id)
    except Exception as exc:                   # noqa: BLE001 — раннер бывает любым
        raise _fail(exc) from exc


# --------------------------------------------------------------- удаление


@router.delete("/web/episodes/{subject_id}")
async def web_delete_episode(subject_id: str, request: Request,
                             orphan_ok: int = Query(0, ge=0, le=1)):
    """Удалить эпизод целиком: наблюдения, сырьё, след в индексе и реестры.

    Поправка D1. Ключ сырья у OSIRIS — `sha256(source_id|url)`, поэтому один
    адрес, прочитанный в двух эпизодах, это ОДИН файл, подписанный первым
    субъектом. Удаление второго эпизода молча выбило бы улику из-под первого,
    и цитата в старом ответе перестала бы доказываться — без единого сообщения.
    Поэтому пересечение считается ДО удаления, отдаётся списком, и продолжить
    можно только явным `?orphan_ok=1`: чем жертвовать — решение владельца, а не
    умолчание кода.
    """
    svc = _svc(request)
    config._require_enabled()                                  # noqa: SLF001
    subject = (subject_id or "").strip()
    if not subject or "/" in subject or len(subject) > osiris.MAX_SUBJECT:
        raise HTTPException(400, {"message": "негодный субъект эпизода",
                                  "code": "bad_subject"})
    st = _store(svc)
    shared = _shared_raw(st, subject)
    if shared and not orphan_ok:
        raise HTTPException(409, {
            "message": ("это сырьё делят другие эпизоды: удаление лишит их доказательств. "
                        "Повторите с ?orphan_ok=1, если готовы на это"),
            "code": "raw_shared", "shared": shared,
            "affected": sorted({s for item in shared for s in item["subjects"]})})

    report = st.delete_subject(subject)
    runs = ledger.Ledger.delete_for_subject(svc, subject)
    pruned = prune_raw(svc)
    await _emit(svc, "web.episode_deleted", subject=subject,
                observations=report.get("observations_deleted", 0),
                raw=report.get("raw_deleted", 0), runs=len(runs),
                orphan_ok=bool(orphan_ok))
    return {"ok": True, **report, "ledgers_deleted": runs,
            "shared_raw_sacrificed": shared if orphan_ok else [],
            "prune": pruned}
