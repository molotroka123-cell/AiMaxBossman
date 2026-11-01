"""Уроки (lessons) с источниками и датами — поверх СУЩЕСТВУЮЩЕЙ таблицы `facts`.

Владелец 31.10: «штатная память с источниками и датами … Не создавай второй
backend или память. Запись урока не называй доказанным обучением.»

Поэтому здесь нет новой таблицы и нового хранилища: урок — это строка `facts`
с `source_kind="lesson"`, а его проверки — тоже строки `facts`
(`source_kind="lesson_check"`). Правило модуля `facts.py` соблюдается
буквально: строки только ВСТАВЛЯЮТСЯ, UPDATE нет ни одного.

Поля урока (лежат в колонках `facts` и в `facts.meta`):

  source        URL (http/https), `codex-session:<id>`, `claude-session:<id>`,
                `bossman-run:<id>` или `file:<путь>` — без источника урок не пишется
  observed_at   когда автор наблюдал утверждение (обязательно, ISO-дата)
  valid_from    с какой даты утверждение правда в мире → колонка `valid_at`
  author        codex | claude | bossman | owner
  status        ВЫЧИСЛЯЕТСЯ, писатель его не задаёт:
                  lesson_recorded  — урок записан. Это НЕ доказанное обучение.
                  verified_by_test — последняя связанная проверка (eval) прошла;
                                     её id хранится (`eval_ref` + отпечаток квитанции)
                  refuted          — урок опровергнут проверкой/источником

Статус получается из последнего решающего события-проверки. Поставить
`verified_by_test` можно только через `record_eval_result(...)`, а он требует
ссылку на проверку, которую СЕРВЕР сам разрешил (выполнил упражнение
чекером / нашёл решённую оценку скилла). Сам факт записи урока статус не
меняет никогда.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from collections import deque
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Iterable

import sqlalchemy as sa

from ...db import facts as facts_t
from ...db import utcnow
from .facts import FactFormError, public_fact, to_naive_utc

# ------------------------------------------------------------------ словари

SCHEMA = "lesson.v1"
LESSON_KIND = "lesson"
CHECK_KIND = "lesson_check"

AUTHORS = ("codex", "claude", "bossman", "owner")
#: Облачные/внешние писатели: им нельзя в приватные категории владельца.
REMOTE_AUTHORS = frozenset({"codex", "claude"})

STATUS_RECORDED = "lesson_recorded"
STATUS_VERIFIED = "verified_by_test"
STATUS_REFUTED = "refuted"
STATUSES = (STATUS_RECORDED, STATUS_VERIFIED, STATUS_REFUTED)

#: Рабочие категории — сюда может писать кто угодно из AUTHORS.
WORK_CATEGORIES = frozenset({
    "coding", "browser", "pc_control", "business", "research",
    "ops", "memory", "safety", "general",
})
#: Приватные категории владельца — только автор `owner` с локального адреса.
OWNER_PRIVATE_CATEGORIES = frozenset({
    "personal", "owner_private", "finance_private", "health", "family",
    "contacts", "chats", "credentials",
})
CATEGORIES = WORK_CATEGORIES | OWNER_PRIVATE_CATEGORIES

MAX_TITLE = 160
MAX_BODY = 4000
MAX_SOURCE = 500
MAX_EXPECT = 200
MAX_TAGS = 8
#: Ограничения урока (01.11): где урок НЕ проверен / не применим. Хранятся в `facts.meta`,
#: переживают рестарт вместе с уроком, в content hash не входят (тот же урок = тот же id).
MAX_LIMITATIONS = 6
MAX_LIMITATION = 300
#: Максимальный размер JSON-тела запроса урока (байт) — проверяется в API.
MAX_REQUEST_BYTES = 16_384

#: Проверки свежести и статуса — сколько дней урок считается свежим по умолчанию.
DEFAULT_STALE_DAYS = 30

_SESSION_RE = re.compile(r"^(codex-session|claude-session|bossman-run):[A-Za-z0-9._:\-]{1,128}$")
_FILE_RE = re.compile(r"^file:[^\x00\r\n]{1,400}$")
_URL_RE = re.compile(r"^https?://[^\s<>\"']{3,480}$", re.I)
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,39}$")


class LessonError(ValueError):
    """Урок отклонён правилами (422). Текст — для писателя, по-русски."""


class LessonForbidden(LessonError):
    """Запрет по правам (403): облачный писатель → приватная категория и т.п."""


# ------------------------------------------------------------------ помощники

def _canon(text: Any) -> str:
    return " ".join(unicodedata.normalize("NFC", str(text or "")).split())


def content_hash(*, title: str, body: str, category: str, source: str) -> str:
    """Ключ идемпотентности: одинаковый урок из того же источника = тот же урок.

    Регистр и Unicode-форма не делают урок новым (как `fact_hash`)."""
    blob = "\x1f".join(
        " ".join(unicodedata.normalize("NFKC", str(p or "")).casefold().split())
        for p in (category, title, body, source))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _redact(text: str) -> tuple[str, bool]:
    """Секреты вычищаются ДО записи (pult_report.redact = plugin_security + свои
    шаблоны). Возвращает (текст, были_ли_секреты)."""
    from ...features.pult_report import redact  # поздний импорт: features тянет tools
    clean = redact(text or "")
    return clean, clean != (text or "")


def validate_source(source: str) -> str:
    src = _canon(source)
    if not src:
        raise LessonError("урок без источника не принимается: нужен source — URL, "
                          "codex-session:<id>, claude-session:<id>, bossman-run:<id> или file:<путь>")
    if len(src) > MAX_SOURCE:
        raise LessonError(f"source длиннее {MAX_SOURCE} символов")
    if _URL_RE.match(src) or _SESSION_RE.match(src) or _FILE_RE.match(src):
        if _URL_RE.match(src):
            from ...plugin_security import PluginSecurityError, validate_url
            try:
                validate_url(src)
            except PluginSecurityError as exc:
                raise LessonError(f"source-URL отклонён политикой: {exc}") from None
        return src
    raise LessonError("source не распознан: ожидается http(s)-URL, codex-session:<id>, "
                      "claude-session:<id>, bossman-run:<id> или file:<путь>")


def _date(value: Any, field_name: str) -> datetime:
    if value in (None, ""):
        raise LessonError(f"урок без даты не принимается: нужен {field_name} (ISO, например 2026-10-31)")
    try:
        return to_naive_utc(value)
    except FactFormError:
        raise LessonError(f"{field_name}: не разобрал дату {value!r}; нужен ISO 2026-10-31") from None


def _is_loopback(host: str | None) -> bool:
    h = str(host or "").strip().lower()
    return h in {"127.0.0.1", "::1", "localhost", "testclient", "test"} or h.startswith("127.")


# ------------------------------------------------------------------ валидация

def prepare_lesson(payload: dict, *, client_host: str | None = None,
                   remote_hint: bool = False, now: datetime | None = None) -> dict:
    """Проверить и нормализовать урок. Ничего не пишет. Бросает LessonError/LessonForbidden.

    `remote_hint=True` — вызов пришёл от облачного исполнителя (инструмент в
    облачном прогоне, заголовок X-Bossman-Writer: cloud): такие писатели в
    приватные категории не пишут, даже назвавшись owner.
    """
    if not isinstance(payload, dict):
        raise LessonError("урок — это JSON-объект")
    now = now or utcnow()

    # Статус не задаётся писателем. Записанный урок ≠ доказанное обучение.
    status = payload.get("status")
    if status not in (None, "", STATUS_RECORDED):
        raise LessonError(
            f"status={status!r} нельзя задать при записи: запись урока не является "
            f"доказанным обучением. Статус {STATUS_VERIFIED} ставит только сервер после "
            f"проверки eval: POST /api/memory/lessons/{{id}}/verify")
    for forbidden in ("verified", "eval_ref", "eval_id", "verified_at"):
        if payload.get(forbidden) not in (None, "", False):
            raise LessonError(
                f"поле {forbidden} нельзя прислать вместе с уроком: проверку выполняет сервер "
                f"через /verify, по ссылке на реальный прогон проверки")

    author = _canon(payload.get("author")).lower()
    if author not in AUTHORS:
        raise LessonError(f"author должен быть одним из {', '.join(AUTHORS)}")
    category = _canon(payload.get("category") or "general").lower()
    if category not in CATEGORIES:
        raise LessonError(f"неизвестная категория {category!r}; рабочие: "
                          f"{', '.join(sorted(WORK_CATEGORIES))}")
    remote = remote_hint or author in REMOTE_AUTHORS or not _is_loopback(client_host)
    if category in OWNER_PRIVATE_CATEGORIES and (remote or author != "owner"):
        raise LessonForbidden(
            f"категория {category!r} — приватная владельца: облачный/удалённый писатель "
            f"(author={author}, remote={remote}) туда не пишет")

    title_raw = _canon(payload.get("title"))
    body_raw = str(payload.get("body") or payload.get("lesson") or "").strip()
    if not title_raw:
        raise LessonError("нужен title — короткое название урока")
    if not body_raw:
        raise LessonError("нужен body — текст урока")
    if len(title_raw) > MAX_TITLE:
        raise LessonError(f"title длиннее {MAX_TITLE} символов")
    if len(body_raw) > MAX_BODY:
        raise LessonError(f"урок слишком большой: {len(body_raw)} символов, максимум {MAX_BODY}")

    source = validate_source(payload.get("source") or "")
    observed = _date(payload.get("observed_at"), "observed_at")
    valid_from = _date(payload.get("valid_from") or payload.get("observed_at"), "valid_from")
    if observed > now + timedelta(days=1):
        raise LessonError("observed_at в будущем: наблюдение не может быть позже сегодняшнего дня")
    if valid_from > now + timedelta(days=1):
        raise LessonError("valid_from в будущем: урок про будущее — это план, а не урок")

    expect = _canon(payload.get("expect_text"))
    if len(expect) > MAX_EXPECT:
        raise LessonError(f"expect_text длиннее {MAX_EXPECT} символов")
    tags_in = payload.get("tags") or []
    if not isinstance(tags_in, list) or len(tags_in) > MAX_TAGS:
        raise LessonError(f"tags — список до {MAX_TAGS} коротких меток")
    tags = []
    for tag in tags_in:
        t = _canon(tag).lower()
        if not _TAG_RE.match(t):
            raise LessonError(f"метка {tag!r}: только a-z0-9_.- до 40 символов")
        tags.append(t)
    limitations_in = payload.get("limitations")
    if limitations_in in (None, "", []):
        limitations_in = []
    elif isinstance(limitations_in, str):
        limitations_in = [limitations_in]
    if not isinstance(limitations_in, list) or len(limitations_in) > MAX_LIMITATIONS:
        raise LessonError(f"limitations — строка или список до {MAX_LIMITATIONS} строк")
    limitations: list[str] = []
    red_l = False
    for item in limitations_in:
        if not isinstance(item, str) or not _canon(item):
            raise LessonError("limitations: каждое ограничение — непустая строка")
        if len(_canon(item)) > MAX_LIMITATION:
            raise LessonError(f"limitations: ограничение длиннее {MAX_LIMITATION} символов")
        clean, red = _redact(_canon(item))
        limitations.append(clean)
        red_l = red_l or red

    title, red_t = _redact(title_raw)
    body, red_b = _redact(body_raw)
    source_clean, red_s = _redact(source)
    expect_clean, red_e = _redact(expect)
    session_id = _canon(payload.get("session_id"))[:128]
    return {
        "title": title, "body": body, "category": category, "author": author,
        "source": source_clean, "observed_at": observed, "valid_from": valid_from,
        "expect_text": expect_clean, "tags": tags, "session_id": session_id,
        "limitations": limitations,
        "remote": bool(remote),
        "redacted": bool(red_t or red_b or red_s or red_e or red_l),
        "hash": content_hash(title=title, body=body, category=category, source=source_clean),
    }


# ------------------------------------------------------------------ запись

def _rows(result: Any) -> list[dict]:
    return [dict(r._mapping) for r in result]


def _scope_for(category: str) -> str:
    return "personal" if category in OWNER_PRIVATE_CATEGORIES else "work"


async def _find_by_hash(session, digest: str, category: str) -> dict | None:
    rows = _rows(await session.execute(
        sa.select(facts_t).where(sa.and_(
            facts_t.c.source_kind == LESSON_KIND,
            facts_t.c.subject == f"lesson:{category}",
            facts_t.c.object == digest))))
    return rows[0] if rows else None


async def write_lesson(session, lesson: dict) -> dict:
    """Идемпотентная вставка урока (ключ — content hash). Только INSERT."""
    existing = await _find_by_hash(session, lesson["hash"], lesson["category"])
    if existing is not None:
        return {"lesson": existing, "deduped": True}
    now = utcnow()
    meta = {
        "kind": LESSON_KIND, "schema": SCHEMA,
        "title": lesson["title"], "category": lesson["category"],
        "author": lesson["author"], "source": lesson["source"],
        "observed_at": lesson["observed_at"].isoformat(),
        "valid_from": lesson["valid_from"].isoformat(),
        "expect_text": lesson["expect_text"], "tags": lesson["tags"],
        "session_id": lesson["session_id"], "remote_writer": lesson["remote"],
        "limitations": list(lesson.get("limitations") or []),
        "redacted": lesson["redacted"], "hash": lesson["hash"],
        "scope": _scope_for(lesson["category"]),
        # Записанный урок — НЕ доказанное обучение (владелец 31.10).
        "recorded_is_not_proven_learning": True,
    }
    res = await session.execute(sa.insert(facts_t).values(
        subject=f"lesson:{lesson['category']}", predicate=lesson["title"][:160],
        object=lesson["hash"], statement=lesson["body"],
        valid_at=lesson["valid_from"], invalid_at=None, created_at=now,
        expired_at=None, superseded_by=None, source_kind=LESSON_KIND,
        source_run_id=None, source_note=lesson["source"][:500], confidence=1.0,
        meta=meta))
    new_id = int(res.inserted_primary_key[0])
    await session.commit()
    row = _rows(await session.execute(sa.select(facts_t).where(facts_t.c.id == new_id)))[0]
    return {"lesson": row, "deduped": False}


async def get_lesson_row(session, lesson_id: int) -> dict | None:
    rows = _rows(await session.execute(sa.select(facts_t).where(sa.and_(
        facts_t.c.id == int(lesson_id), facts_t.c.source_kind == LESSON_KIND))))
    return rows[0] if rows else None


async def check_rows(session, lesson_id: int) -> list[dict]:
    return _rows(await session.execute(
        sa.select(facts_t).where(sa.and_(
            facts_t.c.source_kind == CHECK_KIND,
            facts_t.c.subject == f"lesson-check:{int(lesson_id)}"))
        .order_by(facts_t.c.created_at.asc(), facts_t.c.id.asc())))


async def _insert_check(session, lesson_id: int, *, predicate: str, outcome: str,
                        ref: str, statement: str, meta: dict) -> dict:
    now = utcnow()
    payload = dict(meta)
    payload.update({"kind": CHECK_KIND, "lesson_id": int(lesson_id), "outcome": outcome,
                    "checked_at": now.isoformat(), "scope": "work"})
    res = await session.execute(sa.insert(facts_t).values(
        subject=f"lesson-check:{int(lesson_id)}", predicate=predicate, object=ref[:500],
        statement=statement, valid_at=now, invalid_at=None, created_at=now,
        expired_at=None, superseded_by=None, source_kind=CHECK_KIND,
        source_run_id=None, source_note=ref[:500], confidence=1.0, meta=payload))
    new_id = int(res.inserted_primary_key[0])
    await session.commit()
    return _rows(await session.execute(sa.select(facts_t).where(facts_t.c.id == new_id)))[0]


# ------------------------------------------------------------------ статус

def _meta(row: dict) -> dict:
    m = row.get("meta")
    if isinstance(m, str):
        try:
            m = json.loads(m)
        except ValueError:
            m = {}
    return m if isinstance(m, dict) else {}


def derive_status(checks: Iterable[dict]) -> dict:
    """Статус урока из событий проверки. Последнее решающее событие выигрывает.

    passed  → verified_by_test (eval_ref и отпечаток квитанции сохраняются)
    failed  → lesson_recorded (проверка не прошла — урок не подтверждён)
    refuted → refuted
    Свежесть по вебу (`lesson.freshness`) статус НЕ меняет: подтверждение
    страницей ≠ прошедший тест.
    """
    status = STATUS_RECORDED
    eval_ref = None
    receipt = None
    verified_at = None
    last_verified_web = None
    last_web_outcome = None
    for row in checks:
        m = _meta(row)
        if row.get("predicate") == "lesson.verification":
            outcome = m.get("outcome")
            if outcome == "passed":
                status, eval_ref = STATUS_VERIFIED, m.get("eval_ref")
                receipt, verified_at = m.get("receipt_sha256"), m.get("checked_at")
            elif outcome == "failed":
                status, eval_ref, receipt, verified_at = STATUS_RECORDED, None, None, None
            elif outcome == "refuted":
                status, eval_ref, receipt, verified_at = STATUS_REFUTED, m.get("eval_ref"), None, None
        elif row.get("predicate") == "lesson.freshness":
            last_web_outcome = m.get("outcome")
            if m.get("outcome") == "confirmed":
                last_verified_web = m.get("checked_at")
    return {"status": status, "eval_ref": eval_ref, "eval_receipt_sha256": receipt,
            "verified_at": verified_at, "web_last_verified_at": last_verified_web,
            "web_last_outcome": last_web_outcome}


def public_lesson(row: dict, checks: list[dict], *, stale_days: int = DEFAULT_STALE_DAYS,
                  now: datetime | None = None) -> dict:
    m = _meta(row)
    st = derive_status(checks)
    now = now or utcnow()
    anchor_iso = st["web_last_verified_at"] or m.get("observed_at")
    try:
        anchor = to_naive_utc(anchor_iso)
        age_days = (now - anchor).total_seconds() / 86400.0
    except FactFormError:
        age_days = None
    base = public_fact(row)
    return {
        "id": row.get("id"), "title": m.get("title") or row.get("predicate"),
        "body": row.get("statement"), "category": m.get("category"),
        "author": m.get("author"), "source": m.get("source"),
        "observed_at": m.get("observed_at"), "valid_from": m.get("valid_from"),
        "recorded_at": row.get("created_at"), "expect_text": m.get("expect_text") or "",
        "tags": m.get("tags") or [], "session_id": m.get("session_id") or "",
        "limitations": list(m.get("limitations") or []),
        "remote_writer": bool(m.get("remote_writer")),
        "content_hash": m.get("hash"), "redacted": bool(m.get("redacted")),
        "scope": base.get("scope"), "lifecycle": base.get("status"),
        **st,
        "age_days": round(age_days, 2) if age_days is not None else None,
        "stale": bool(age_days is None or age_days > stale_days),
        "stale_after_days": stale_days,
        "proven_learning": False,
        "note_ru": ("Запись урока ≠ доказанное обучение. verified_by_test значит только: "
                    "связанная проверка прошла в момент verified_at."),
        "checks": [{"id": c.get("id"), "kind": c.get("predicate"), **{
            k: _meta(c).get(k) for k in ("outcome", "eval_ref", "receipt_sha256",
                                         "checked_at", "url", "reason")}} for c in checks],
    }


# ------------------------------------------------------------------ проверки

async def record_eval_result(session, lesson_id: int, *, eval_ref: str, passed: bool,
                             receipt_sha256: str, detail: dict | None = None) -> dict:
    """Записать исход ПРОВЕРКИ, которую сервер выполнил/разрешил сам.

    Вызывать только из кода, который действительно получил квитанцию проверки
    (чекер упражнения, решённая skill_evaluation). Пустые ссылки отклоняются."""
    if not eval_ref or not str(eval_ref).strip():
        raise LessonError("verified без ссылки на проверку невозможен: нужен eval_ref")
    if not re.fullmatch(r"[0-9a-f]{64}", str(receipt_sha256 or "")):
        raise LessonError("нужна квитанция проверки (sha256)")
    if await get_lesson_row(session, lesson_id) is None:
        raise LessonError(f"урока {lesson_id} нет")
    outcome = "passed" if passed else "failed"
    return await _insert_check(
        session, lesson_id, predicate="lesson.verification", outcome=outcome,
        ref=str(eval_ref),
        statement=f"Проверка урока {lesson_id} по {eval_ref}: {outcome}",
        meta={"eval_ref": str(eval_ref), "receipt_sha256": receipt_sha256,
              "detail": dict(detail or {})})


async def record_refutation(session, lesson_id: int, *, reason: str, source: str,
                            author: str) -> dict:
    reason_c, _ = _redact(_canon(reason))
    if not reason_c:
        raise LessonError("опровержение без причины не принимается")
    src = validate_source(source)
    if author not in AUTHORS:
        raise LessonError(f"author должен быть одним из {', '.join(AUTHORS)}")
    if await get_lesson_row(session, lesson_id) is None:
        raise LessonError(f"урока {lesson_id} нет")
    return await _insert_check(
        session, lesson_id, predicate="lesson.verification", outcome="refuted",
        ref=src, statement=f"Урок {lesson_id} опровергнут: {reason_c[:300]}",
        meta={"eval_ref": src, "reason": reason_c[:500], "author": author})


Fetcher = Callable[[str], Awaitable[str]]


async def safe_fetch_text(url: str, *, max_bytes: int = 400_000, timeout: float = 10.0) -> str:
    """GET с политикой SSRF Bossman: validate_url + закреплённый IP (анти-rebinding),
    без редиректов, с потолком размера. Только чтение."""
    import httpx

    from ...plugin_security import PinnedTransport, resolve_pinned_ip, validate_url
    from ...html_text import extract

    checked, host = validate_url(url)
    ip = resolve_pinned_ip(host)
    async with httpx.AsyncClient(transport=PinnedTransport({host: ip}), timeout=timeout,
                                 follow_redirects=False,
                                 headers={"User-Agent": "BossmanLessonFreshness/1.0"}) as client:
        async with client.stream("GET", checked) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    break
                chunks.append(chunk)
    raw = b"".join(chunks).decode("utf-8", errors="replace")
    try:
        return extract(raw, base_url=checked).text
    except Exception:  # noqa: BLE001 — грубый текст лучше, чем ничего
        return raw


def _fold(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text or "").casefold().replace("ё", "е").split())


async def check_freshness(session, lesson_id: int, *, fetcher: Fetcher | None = None,
                          url: str | None = None) -> dict:
    """Сверить урок с вебом: скачать источник (или `url`) и найти `expect_text`.

    confirmed   — текст найден → обновляется web_last_verified_at
    not_found   — страница есть, текста нет → урок, вероятно, устарел
    unreachable — сеть/политика не дали скачать
    no_check_text — у урока нет expect_text: проверять нечего
    Статус verified_by_test эта проверка НЕ ставит.
    """
    row = await get_lesson_row(session, lesson_id)
    if row is None:
        raise LessonError(f"урока {lesson_id} нет")
    m = _meta(row)
    target = _canon(url) or str(m.get("source") or "")
    if not _URL_RE.match(target):
        raise LessonError("для проверки свежести нужен http(s)-источник урока или url")
    validate_source(target)
    expect = str(m.get("expect_text") or "")
    started = time.monotonic()
    if not expect:
        outcome, reason = "no_check_text", "у урока нет expect_text"
    else:
        try:
            text = await (fetcher or safe_fetch_text)(target)
            outcome = "confirmed" if _fold(expect) in _fold(text) else "not_found"
            reason = "" if outcome == "confirmed" else "ожидаемый текст не найден на странице"
        except Exception as exc:  # noqa: BLE001 — исход проверки, не падение
            outcome, reason = "unreachable", type(exc).__name__
    return await _insert_check(
        session, lesson_id, predicate="lesson.freshness", outcome=outcome, ref=target,
        statement=f"Свежесть урока {lesson_id}: {outcome}",
        meta={"url": target, "reason": reason,
              "elapsed_ms": round((time.monotonic() - started) * 1000, 1)})


async def list_lessons(session, *, category: str | None = None, author: str | None = None,
                       status: str | None = None, include_private: bool = False,
                       stale_days: int = DEFAULT_STALE_DAYS, only_stale: bool = False,
                       limit: int = 100, now: datetime | None = None) -> list[dict]:
    q = sa.select(facts_t).where(facts_t.c.source_kind == LESSON_KIND)
    if category:
        q = q.where(facts_t.c.subject == f"lesson:{_canon(category).lower()}")
    q = q.order_by(facts_t.c.id.desc()).limit(1000)
    out: list[dict] = []
    for row in _rows(await session.execute(q)):
        m = _meta(row)
        if not include_private and m.get("category") in OWNER_PRIVATE_CATEGORIES:
            continue
        if author and m.get("author") != author:
            continue
        item = public_lesson(row, await check_rows(session, int(row["id"])),
                             stale_days=stale_days, now=now)
        if status and item["status"] != status:
            continue
        if only_stale and not item["stale"]:
            continue
        out.append(item)
        if len(out) >= max(1, min(int(limit), 500)):
            break
    return out


# ------------------------------------------------------------------ лимит частоты

class RateLimiter:
    """Скользящее окно на ключ (автор). Память процесса — после рестарта окно пустое."""

    def __init__(self, per_minute: int = 30, per_hour: int = 300):
        self.per_minute = per_minute
        self.per_hour = per_hour
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str, *, now: float | None = None) -> str | None:
        t = time.monotonic() if now is None else now
        q = self._hits.setdefault(key, deque())
        while q and t - q[0] > 3600:
            q.popleft()
        last_minute = sum(1 for x in q if t - x <= 60)
        if last_minute >= self.per_minute:
            return f"не чаще {self.per_minute} уроков в минуту от {key}"
        if len(q) >= self.per_hour:
            return f"не чаще {self.per_hour} уроков в час от {key}"
        q.append(t)
        return None


__all__ = [
    "AUTHORS", "REMOTE_AUTHORS", "STATUSES", "STATUS_RECORDED", "STATUS_VERIFIED",
    "STATUS_REFUTED", "WORK_CATEGORIES", "OWNER_PRIVATE_CATEGORIES", "CATEGORIES",
    "MAX_BODY", "MAX_REQUEST_BYTES", "DEFAULT_STALE_DAYS",
    "LessonError", "LessonForbidden", "RateLimiter",
    "content_hash", "validate_source", "prepare_lesson", "write_lesson",
    "get_lesson_row", "check_rows", "derive_status", "public_lesson",
    "record_eval_result", "record_refutation", "check_freshness", "safe_fetch_text",
    "list_lessons",
]
