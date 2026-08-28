"""V2.2 — факты с двумя осями времени.

Идея взята из `getzep/graphiti` (docs/research/graphiti.md, §6 и §11), форма
факта — из `mem0ai/mem0` (docs/research/mem0.md, §5, приёмы И-1…И-5).
**Ни одна из библиотек не импортируется и не устанавливается.** Здесь только
наш код поверх таблицы `facts`, схемой которой владеет `bcc/db.py`.

Две оси времени (все четыре поля живут в `facts`):

  ось мира      valid_at    — когда утверждение СТАЛО правдой
                invalid_at  — когда оно ПЕРЕСТАЛО быть правдой
  ось знания    created_at  — когда мы об этом УЗНАЛИ
                expired_at  — когда мы ПОМЕТИЛИ факт устаревшим

Правила, которые здесь соблюдаются буквально:

  * **UPDATE содержимого запрещён.** Замена факта — это ВСТАВКА нового и
    «закрытие» старого: `invalid_at = valid_at нового` (а НЕ «сейчас» —
    именно это отличает темпоральность от журнала изменений), `expired_at =
    now()`, `superseded_by = id нового`. Единственный UPDATE во всём модуле —
    `_close_fact()`, и он трогает ровно эти три служебные колонки.
  * **Ноль вызовов LLM.** Факты пишутся явно — человеком или агентом через
    инструмент. `harvest()` — детерминированный парсер по итоговым сообщениям
    run'а, один раз на run, без обращения к модели.
  * **Устаревшее не прячется молча.** `render_for_model()` отдаёт факт вместе
    с метками времени: «раньше было А, с июня — Б».
  * **Модель не видит настоящих id строк** (И-3): в выдаче они подменяются на
    0, 1, 2 …, обратное отображение живёт в `id_map`.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

import sqlalchemy as sa

from ...db import checkpoints as checkpoints_t
from ...db import facts as facts_t
from ...db import interventions as interventions_t
from ...db import utcnow

# ------------------------------------------------------------------ И-1: форма

MIN_WORDS = 15
MAX_WORDS = 80

#: Анафорические местоимения: самодостаточный факт не может на них опираться —
#: через полгода некому будет спросить, кто такой «он». «это» намеренно НЕ
#: включено: в русском это чаще связка («SQLite — это файл»), чем отсылка.
PRONOUNS = frozenset("""
он она оно они его её ее их ему ей им ими них нём нем ней нее неё
себя себе собой свой своя своё свое свои своего своих
этот эта эти этого этих этом той тот та те того тех
тут там тогда туда сюда здесь оттуда
я меня мне мной мы нас нам нами мой моя мои наш наша наши
ты тебя тебе тобой вы вас вам вами твой твоя ваш ваша ваши
he she it they them him her his its their this that these those there then
i me my we us our you your
""".split())

#: Относительные даты. Их не запрещаем — их ПРИВЯЗЫВАЕМ к дате наблюдения
#: (`anchor_relative_dates`), а всё, что осталось непривязанным, отклоняем.
_RELATIVE: list[tuple[re.Pattern[str], int, str]] = [
    (re.compile(r"\bпозавчера\b", re.I), -2, "{d}"),
    (re.compile(r"\bвчера\b", re.I), -1, "{d}"),
    (re.compile(r"\bсегодня\b", re.I), 0, "{d}"),
    (re.compile(r"\bзавтра\b", re.I), 1, "{d}"),
    (re.compile(r"\bпослезавтра\b", re.I), 2, "{d}"),
    (re.compile(r"\bна\s+прошлой\s+неделе\b", re.I), -7, "на неделе от {d}"),
    (re.compile(r"\bна\s+этой\s+неделе\b", re.I), 0, "на неделе от {d}"),
    (re.compile(r"\bв\s+прошлом\s+месяце\b", re.I), -30, "в месяце до {d}"),
    (re.compile(r"\bв\s+прошлом\s+году\b", re.I), -365, "в году до {d}"),
    (re.compile(r"\byesterday\b", re.I), -1, "{d}"),
    (re.compile(r"\btoday\b", re.I), 0, "{d}"),
    (re.compile(r"\btomorrow\b", re.I), 1, "{d}"),
    (re.compile(r"\blast\s+week\b", re.I), -7, "week of {d}"),
]

#: Переход обязан сохраняться: «перешёл с X на Y», а не «использует Y».
TRANSITION_MARKERS = (
    "перешёл", "перешел", "перешли", "перешла", "переход", "переехал", "переехали",
    "сменил", "сменили", "заменил", "заменили", "вместо", "раньше", "ранее",
    "до этого", "с версии", "switched", "moved from", "replaced", "instead of",
)

#: Токен считаем именем собственным / числом, если он капитализирован,
#: содержит цифру или похож на идентификатор (`bcc/db.py`, `v2.1-phase-E`).
_PROPER_RE = re.compile(r"[A-ZА-ЯЁ][\w\-.]{1,}|[\w\-.]*\d[\w\-./]*|[\w\-]+[./][\w\-./]+")
_WORD_RE = re.compile(r"[\w\-']+", re.UNICODE)


class FactFormError(ValueError):
    """Факт не прошёл проверку формы (И-1). Это данные для модели, не падение."""


class FactWriteError(ValueError):
    """Некорректная операция записи (перекрыть несуществующий/уже закрытый факт)."""


# ------------------------------------------------------------------ время

def to_naive_utc(value: Any) -> datetime:
    """Любой разумный вход → наивный UTC, как во всей остальной схеме."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value or "").strip()
    if not text:
        raise FactFormError("нужна дата valid_at (когда утверждение стало правдой)")
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return to_naive_utc(datetime.fromisoformat(text))
    except ValueError:
        raise FactFormError(
            f"не разобрал дату {value!r}; ожидается ISO: 2026-06-01 или "
            f"2026-06-01T10:00:00") from None


def anchor_relative_dates(statement: str, observed_at: datetime) -> str:
    """«вчера» → конкретная дата, посчитанная от ДАТЫ НАБЛЮДЕНИЯ, не от «сегодня».

    Именно это требование mem0 (§ Memory Quality Standards) и делает факт
    читаемым через год: относительные даты в архиве бессмысленны.
    """
    out = statement
    for pattern, shift, template in _RELATIVE:
        if not pattern.search(out):
            continue
        stamp = (observed_at + timedelta(days=shift)).strftime("%Y-%m-%d")
        out = pattern.sub(template.format(d=stamp), out)
    return out


# ------------------------------------------------------------------ И-1: проверка

def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def proper_tokens(*parts: str) -> list[str]:
    """Имена собственные и числа, которые нельзя обобщать."""
    seen: list[str] = []
    for part in parts:
        for token in _PROPER_RE.findall(part or ""):
            token = token.strip(".-")
            if len(token) > 1 and token.lower() not in seen:
                seen.append(token.lower())
    return seen


def validate_statement(statement: str, *, subject: str = "", object: str = "",
                       supersedes: Any = None, observed_at: datetime | None = None,
                       ) -> str:
    """И-1: форма самодостаточного факта. Возвращает нормализованный текст.

    Пять проверок, все детерминированные и без модели:
      1. непустой текст;
      2. 15–80 слов (короче — не самодостаточно, длиннее — это уже заметка);
      3. никаких анафорических местоимений;
      4. никаких неразрешённых относительных дат;
      5. имена собственные и числа из subject/object не обобщены — они
         присутствуют в тексте дословно;
      6. если факт кого-то перекрывает — переход назван явно.
    """
    text = " ".join(str(statement or "").split())
    if not text:
        raise FactFormError("пустой факт: нужно самодостаточное утверждение")

    if observed_at is not None:
        text = anchor_relative_dates(text, observed_at)

    words = _words(text)
    if len(words) < MIN_WORDS:
        raise FactFormError(
            f"факт слишком короткий ({len(words)} сл.): нужно {MIN_WORDS}–{MAX_WORDS} "
            f"слов самодостаточного утверждения, понятного без контекста диалога")
    if len(words) > MAX_WORDS:
        raise FactFormError(
            f"факт слишком длинный ({len(words)} сл., максимум {MAX_WORDS}): "
            f"это уже заметка — сохраните её через memory.write")

    found = sorted({w for w in (x.lower() for x in words) if w in PRONOUNS})
    if found:
        raise FactFormError(
            f"в факте есть местоимения ({', '.join(found)}): факт читают через год "
            f"без диалога — назовите сущности по именам")

    leftovers = sorted({m.group(0).lower() for pat, _, _ in _RELATIVE
                        for m in [pat.search(text)] if m})
    if leftovers:
        raise FactFormError(
            f"относительные даты ({', '.join(leftovers)}) не привязаны к дате "
            f"наблюдения: укажите абсолютную дату")

    lowered = text.lower()
    missing = [t for t in proper_tokens(subject, object) if t not in lowered]
    if missing:
        raise FactFormError(
            f"имена собственные и числа обобщать нельзя — в тексте нет: "
            f"{', '.join(missing)}")

    if supersedes is not None and not any(m in lowered for m in TRANSITION_MARKERS):
        raise FactFormError(
            "факт перекрывает прежний, значит должен называть переход "
            "(«перешли с X на Y»), а не только новое состояние")
    return text


# ------------------------------------------------------------------ И-2: дедуп

def fact_hash(subject: str, predicate: str, object: str, statement: str) -> str:
    """sha256 нормализованного факта — ключ дедупа (у mem0 md5, берём sha256)."""
    blob = "\x1f".join(" ".join(str(p or "").lower().split())
                       for p in (subject, predicate, object, statement))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def row_hash(row: dict) -> str:
    return fact_hash(row.get("subject", ""), row.get("predicate", ""),
                     row.get("object", ""), row.get("statement", ""))


# ------------------------------------------------------------------ модель Fact

@dataclass(slots=True)
class Fact:
    """Факт как его видит наш код. Зеркало строки `facts`, без ORM."""
    subject: str
    predicate: str
    statement: str
    valid_at: datetime
    object: str = ""
    id: int | None = None
    invalid_at: datetime | None = None
    created_at: datetime | None = None
    expired_at: datetime | None = None
    superseded_by: int | None = None
    source_kind: str = "human"
    source_run_id: int | None = None
    source_note: str = ""
    confidence: float = 1.0
    meta: dict = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: dict) -> "Fact":
        meta = row.get("meta")
        return cls(
            id=row.get("id"), subject=row.get("subject", ""),
            predicate=row.get("predicate", ""), object=row.get("object") or "",
            statement=row.get("statement", ""), valid_at=row.get("valid_at"),
            invalid_at=row.get("invalid_at"), created_at=row.get("created_at"),
            expired_at=row.get("expired_at"), superseded_by=row.get("superseded_by"),
            source_kind=row.get("source_kind") or "human",
            source_run_id=row.get("source_run_id"),
            source_note=row.get("source_note") or "",
            confidence=float(row.get("confidence") or 1.0),
            meta=meta if isinstance(meta, dict) else {})

    @property
    def is_current(self) -> bool:
        return self.invalid_at is None and self.expired_at is None


# ------------------------------------------------------------------ запись

def _rows(result: Any) -> list[dict]:
    return [dict(r._mapping) for r in result]


async def _fetch(session, fact_id: int) -> dict | None:
    res = await session.execute(sa.select(facts_t).where(facts_t.c.id == int(fact_id)))
    row = res.first()
    return dict(row._mapping) if row is not None else None


async def _close_fact(session, old_id: int, *, invalid_at: datetime,
                      superseded_by: int, at: datetime) -> None:
    """ЕДИНСТВЕННЫЙ UPDATE в модуле — и только по трём служебным колонкам.

    Содержимое (subject/predicate/object/statement/valid_at/created_at)
    не трогается никогда: история решений не переписывается задним числом.
    """
    await session.execute(
        sa.update(facts_t).where(facts_t.c.id == int(old_id)).values(
            invalid_at=invalid_at,       # ← valid_at НОВОГО факта, не «сейчас»
            expired_at=at,
            superseded_by=int(superseded_by)))


async def write_fact(session, *, subject: str, predicate: str, statement: str,
                     valid_at: Any, object: str = "", supersedes: int | None = None,
                     conflicts_with: Iterable[int] | None = None,
                     source_kind: str = "human", source_run_id: int | None = None,
                     source_note: str = "", confidence: float = 1.0,
                     meta: dict | None = None, observed_at: Any = None,
                     commit: bool = True) -> dict:
    """Записать факт. Additive: существующие строки не переписываются.

    Возвращает `{"fact": row, "deduped": bool, "superseded": row|None}`.
    """
    subject = " ".join(str(subject or "").split())
    predicate = " ".join(str(predicate or "").split())
    object = " ".join(str(object or "").split())
    if not subject or not predicate:
        raise FactFormError("нужны subject и predicate: факт — это утверждение "
                            "о конкретной сущности")

    valid = to_naive_utc(valid_at)
    observed = to_naive_utc(observed_at) if observed_at is not None else valid
    text = validate_statement(statement, subject=subject, object=object,
                              supersedes=supersedes, observed_at=observed)

    digest = fact_hash(subject, predicate, object, text)

    # И-2: дедуп по хэшу. Колонки под хэш в схеме нет (владелец — bcc/db.py),
    # поэтому сверяем в Python по индексу (subject, predicate) — выборка мала.
    same = _rows(await session.execute(
        sa.select(facts_t).where(sa.and_(facts_t.c.subject == subject,
                                         facts_t.c.predicate == predicate))))
    for row in same:
        if row_hash(row) == digest and row.get("valid_at") == valid:
            return {"fact": row, "deduped": True, "superseded": None}

    old: dict | None = None
    if supersedes is not None:
        old = await _fetch(session, int(supersedes))
        if old is None:
            raise FactWriteError(f"нечего перекрывать: факта {supersedes} нет")
        if old.get("superseded_by") is not None:
            raise FactWriteError(
                f"факт {supersedes} уже перекрыт фактом {old['superseded_by']}: "
                f"перекрывайте самый свежий, история не переписывается")
        if to_naive_utc(old["valid_at"]) > valid:
            raise FactWriteError(
                f"новый факт начинается раньше перекрываемого "
                f"({valid.date()} < {to_naive_utc(old['valid_at']).date()}): "
                f"проверьте valid_at")

    now = utcnow()
    payload = dict(meta or {})
    payload.setdefault("hash", digest)
    payload.setdefault("observed_at", observed.isoformat())
    if supersedes is not None:
        payload["supersedes"] = int(supersedes)
    links = [int(x) for x in (conflicts_with or [])]
    if links:                       # И-2: конфликт помечается ссылкой, не удалением
        payload["conflicts_with"] = links

    res = await session.execute(sa.insert(facts_t).values(
        subject=subject, predicate=predicate, object=object, statement=text,
        valid_at=valid, invalid_at=None, created_at=now, expired_at=None,
        superseded_by=None, source_kind=str(source_kind or "human"),
        source_run_id=source_run_id, source_note=str(source_note or ""),
        confidence=float(confidence), meta=payload))
    new_id = int(res.inserted_primary_key[0])

    if old is not None:
        await _close_fact(session, int(old["id"]), invalid_at=valid,
                          superseded_by=new_id, at=now)

    if commit:
        await session.commit()

    fresh = await _fetch(session, new_id)
    closed = await _fetch(session, int(old["id"])) if old is not None else None
    return {"fact": fresh, "deduped": False, "superseded": closed}


# ------------------------------------------------------------------ чтение

async def query_facts(session, *, subject: str | None = None,
                      predicate: str | None = None, as_of: Any = None,
                      known_as_of: Any = None, include_superseded: bool = False,
                      source_run_id: int | None = None, limit: int = 50) -> list[dict]:
    """Что считаем правдой сейчас / что считали на дату.

    * без `as_of` — только действующие строки (`invalid_at` и `expired_at` пусты);
    * `as_of` — ось МИРА: что было правдой на дату (`valid_at <= T` и
      `invalid_at` пуст или позже T);
    * `known_as_of` — ось ЗНАНИЯ: что мы СЧИТАЛИ правдой на дату (`created_at <= K`
      и `expired_at` пуст или позже K). Две оси комбинируются;
    * `include_superseded=True` — вся история без фильтров валидности.
    """
    query = sa.select(facts_t)
    if subject:
        query = query.where(facts_t.c.subject == " ".join(str(subject).split()))
    if predicate:
        query = query.where(facts_t.c.predicate == " ".join(str(predicate).split()))
    if source_run_id is not None:
        query = query.where(facts_t.c.source_run_id == int(source_run_id))

    if not include_superseded:
        if as_of is not None:
            moment = to_naive_utc(as_of)
            query = query.where(sa.and_(
                facts_t.c.valid_at <= moment,
                sa.or_(facts_t.c.invalid_at.is_(None), facts_t.c.invalid_at > moment)))
        else:
            query = query.where(facts_t.c.invalid_at.is_(None))
        if known_as_of is not None:
            known = to_naive_utc(known_as_of)
            query = query.where(sa.and_(
                facts_t.c.created_at <= known,
                sa.or_(facts_t.c.expired_at.is_(None), facts_t.c.expired_at > known)))
        elif as_of is None:
            # «сейчас» по обеим осям: не помечен устаревшим
            query = query.where(facts_t.c.expired_at.is_(None))

    query = query.order_by(facts_t.c.valid_at.asc(), facts_t.c.id.asc())
    query = query.limit(max(1, min(int(limit), 200)))
    return _rows(await session.execute(query))


# ------------------------------------------------------------------ И-3: маски id

def _fmt(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    return str(value or "")


def render_for_model(rows: Iterable[dict]) -> tuple[str, dict[str, int]]:
    """Список фактов для модели: настоящие id подменены на 0, 1, 2 … (И-3).

    Метки времени отдаются вместе с фактом — устаревшее не прячется молча
    (graphiti §6.5): модель должна видеть «раньше было А, с июня — Б».
    Возвращает `(текст, id_map)`, где `id_map["0"] == настоящий id`.
    """
    rows = list(rows)
    id_map: dict[str, int] = {}
    back: dict[int, str] = {}
    for index, row in enumerate(rows):
        real = row.get("id")
        if real is None:
            continue
        id_map[str(index)] = int(real)
        back[int(real)] = str(index)

    lines: list[str] = []
    for index, row in enumerate(rows):
        head = f"[{index}] {row.get('subject', '')} — {row.get('predicate', '')}"
        if row.get("object"):
            head += f": {row['object']}"
        valid = _fmt(row.get("valid_at"))
        if row.get("invalid_at"):
            world = f"было правдой с {valid} по {_fmt(row['invalid_at'])}"
        else:
            world = f"правда с {valid}, действует сейчас"
        knowledge = f"узнали {_fmt(row.get('created_at'))}"
        if row.get("expired_at"):
            knowledge += f", помечен устаревшим {_fmt(row['expired_at'])}"
        tail = ""
        replaced_by = row.get("superseded_by")
        if replaced_by is not None:
            masked = back.get(int(replaced_by))
            tail = (f" | перекрыт фактом [{masked}]" if masked is not None
                    else " | перекрыт более поздним фактом")
        lines.append(f"{head}\n    {row.get('statement', '')}\n"
                     f"    {world} | {knowledge}{tail}")
    return "\n".join(lines), id_map


def resolve_model_ids(values: Iterable[Any], id_map: dict[str, int]) -> list[int]:
    """Обратное отображение масок в настоящие id. Неизвестное — ошибка, не догадка."""
    out: list[int] = []
    for value in values:
        key = str(value).strip()
        if key not in id_map:
            raise FactWriteError(
                f"ссылка [{key}] не из выданного списка фактов — не угадываю id")
        out.append(id_map[key])
    return out


# ------------------------------------------------------------------ этап 3: лента

async def decision_timeline(session, target_kind: str, target_id: int, *,
                            subject: str | None = None,
                            run_ids: Iterable[int] | None = None,
                            limit: int = 100) -> list[dict]:
    """Read-only лента «что решили и когда это перестало действовать».

    Собирается из УЖЕ существующих `interventions` и новых `facts`.
    Никакого нового хранилища (graphiti §11, этап 3).
    """
    events: list[dict] = []

    rows = _rows(await session.execute(
        sa.select(interventions_t)
        .where(sa.and_(interventions_t.c.target_kind == str(target_kind),
                       interventions_t.c.target_id == int(target_id)))
        .order_by(interventions_t.c.created_at.asc())))
    for row in rows:
        events.append({
            "at": row.get("created_at"), "kind": "intervention",
            "action": row.get("action") or "", "text": row.get("reason") or "",
            "source": "interventions", "ref": row.get("id"),
            "detail": row.get("detail") if isinstance(row.get("detail"), dict) else {},
        })

    wanted = {int(x) for x in (run_ids or [])}
    if str(target_kind) == "run":
        wanted.add(int(target_id))
    fact_rows: list[dict] = []
    if wanted:
        fact_rows += _rows(await session.execute(
            sa.select(facts_t).where(facts_t.c.source_run_id.in_(sorted(wanted)))))
    if subject:
        fact_rows += _rows(await session.execute(
            sa.select(facts_t).where(facts_t.c.subject == " ".join(subject.split()))))
    seen: set[int] = set()
    for row in fact_rows:
        fid = int(row["id"])
        if fid in seen:
            continue
        seen.add(fid)
        events.append({
            "at": row.get("valid_at"), "kind": "fact",
            "action": row.get("predicate") or "", "text": row.get("statement") or "",
            "source": "facts", "ref": fid, "detail": {"object": row.get("object") or ""},
        })
        if row.get("invalid_at") is not None:
            events.append({
                "at": row.get("invalid_at"), "kind": "fact_invalid",
                "action": row.get("predicate") or "",
                "text": f"перестало действовать: {row.get('statement') or ''}",
                "source": "facts", "ref": fid,
                "detail": {"superseded_by": row.get("superseded_by")},
            })

    events.sort(key=lambda e: (e["at"] or datetime.min, str(e["kind"]), e["ref"] or 0))
    return events[:max(1, min(int(limit), 500))]


# ------------------------------------------------------------------ И-5: harvest

#: `ФАКТ: субъект | предикат | объект | утверждение [| 2026-06-01]`
FACT_LINE_RE = re.compile(r"^[ \t>*-]*(?:ФАКТ|FACT)\s*:\s*(.+?)\s*$",
                          re.IGNORECASE | re.MULTILINE)


def extract_fact_candidates(messages: Iterable[dict], *,
                            observed_at: datetime | None = None) -> list[dict]:
    """Детерминированный разбор итоговых сообщений run'а. **Ноль вызовов LLM.**

    Мы не просим модель «вспомнить всё важное» постфактум (mem0 берёт за это
    ~8400 токенов на сохранение): агент по ходу работы сам пишет строки
    `ФАКТ: …`, а harvest их собирает — один раз, в конце run'а (И-5).
    """
    out: list[dict] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "") not in ("assistant", "user", "system"):
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content:
            continue
        for match in FACT_LINE_RE.finditer(content):
            parts = [p.strip() for p in match.group(1).split("|")]
            if len(parts) < 3:
                continue
            valid_at = None
            if len(parts) >= 5:
                valid_at, parts = parts[4], parts[:4]
            if len(parts) == 3:
                subject, predicate, statement = parts
                object_ = ""
            else:
                subject, predicate, object_, statement = parts[:4]
            if not subject or not predicate or not statement:
                continue
            out.append({"subject": subject, "predicate": predicate,
                        "object": object_, "statement": statement,
                        "valid_at": valid_at or (observed_at or utcnow())})
    return out


async def run_messages(session, run_id: int) -> list[dict]:
    """Итоговые сообщения run'а — последний checkpoint. Нового хранилища нет."""
    res = await session.execute(
        sa.select(checkpoints_t.c.messages)
        .where(checkpoints_t.c.run_id == int(run_id))
        .order_by(checkpoints_t.c.step.desc(), checkpoints_t.c.id.desc()).limit(1))
    row = res.first()
    if row is None or not row[0]:
        return []
    raw = row[0]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return [m for m in raw if isinstance(m, dict)] if isinstance(raw, list) else []


async def already_harvested(session, run_id: int) -> int:
    res = await session.execute(
        sa.select(sa.func.count()).select_from(facts_t)
        .where(sa.and_(facts_t.c.source_run_id == int(run_id),
                       facts_t.c.source_kind == "run")))
    return int(res.scalar() or 0)


async def harvest(session, *, run_id: int, messages: Iterable[dict] | None = None,
                  observed_at: Any = None, force: bool = False) -> dict:
    """Один проход по завершённому run'у: кандидаты → проверка формы → запись.

    Идемпотентен дважды: маркером «этот run уже собран» и дедупом по хэшу.
    Вызовов модели — ноль, и в этом весь смысл (И-5).
    """
    observed = to_naive_utc(observed_at) if observed_at is not None else utcnow()
    if not force:
        done = await already_harvested(session, run_id)
        if done:
            return {"written": 0, "deduped": 0, "rejected": [], "skipped": True,
                    "existing": done}

    if messages is None:
        messages = await run_messages(session, run_id)

    written: list[dict] = []
    deduped = 0
    rejected: list[dict] = []
    for candidate in extract_fact_candidates(messages, observed_at=observed):
        try:
            result = await write_fact(
                session, subject=candidate["subject"], predicate=candidate["predicate"],
                object=candidate["object"], statement=candidate["statement"],
                valid_at=candidate["valid_at"], observed_at=observed,
                source_kind="run", source_run_id=int(run_id), commit=False)
        except (FactFormError, FactWriteError) as exc:
            rejected.append({"statement": candidate["statement"][:160],
                             "reason": str(exc)})
            continue
        if result["deduped"]:
            deduped += 1
        else:
            written.append(result["fact"])
    await session.commit()
    return {"written": len(written), "deduped": deduped, "rejected": rejected,
            "skipped": False, "facts": written}


__all__ = [
    "Fact", "FactFormError", "FactWriteError",
    "MIN_WORDS", "MAX_WORDS", "PRONOUNS", "TRANSITION_MARKERS", "FACT_LINE_RE",
    "anchor_relative_dates", "validate_statement", "proper_tokens",
    "fact_hash", "row_hash", "to_naive_utc",
    "write_fact", "query_facts", "decision_timeline",
    "render_for_model", "resolve_model_ids",
    "extract_fact_candidates", "run_messages", "already_harvested", "harvest",
]
