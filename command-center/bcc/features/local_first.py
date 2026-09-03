"""Local-first routing decision (флаг OFF по умолчанию).

Замысел: локальная модель — исполнитель по умолчанию, облако — АПЕЛЛЯЦИЯ, а не
запасной аэродром. Задача уходит наверх только тогда, когда для этого есть
измеримая причина, и причина записывается — иначе через месяц никто не скажет,
на чём именно локальная модель проседает.

Границы модуля (важно):
- здесь только СЛОЙ РЕШЕНИЯ и его журнал. Ни одного вызова модели — ни
  локальной, ни облачной. Исполнение подключит следующий проход, ему хватит
  вердикта;
- решение не авторизует ничего: маршрут по-прежнему за Smart Router и
  cloud-policy (тот же принцип, что у Confidence в bcc/v2/model_intelligence —
  оценка не открывает дверей).

Почему свои типы, а не bcc/v2/model_intelligence.Confidence: тот описывает
доверие к УЖЕ полученному ответу (HIGH/MEDIUM/LOW/UNKNOWN, дискретно), здесь же
нужна доопытная оценка неопределённости задачи как число 0..1, сравнимое с
порогом. Дублирования нет: recommend_escalation даёт рекомендацию, decide() —
вердикт с порогом и причиной.

Пороги — данные, а не константы: лежат в settings, читаются ручкой, меняются
ручкой и только при включённом флаге. Правило «дорогая ошибка» вынесено в
отдельную функцию effective_threshold: его видно и его можно проверить, не
разбирая формулу.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..db import events as events_t, rows_dicts, settings_kv
from . import Feature

FLAG = "BOSSMAN_LOCAL_FIRST_ENABLED"
THRESHOLDS_KEY = "local_first.thresholds"
EVENT_KIND = "local_first.decision"

VERDICTS = ("local", "cloud", "refuse")

# Имена правил попадают в журнал и в статистику — это ответ на вопрос
# «по какой причине поднимали», поэтому они стабильные, а не свободный текст.
RULE_HIGH_STAKES = "high_stakes_uncertainty"   # дорогая ошибка: планка ниже
RULE_UNCERTAINTY = "uncertainty"               # обычная планка неопределённости
RULE_LOCAL_FIRST = "local_first"               # ни одно правило не сработало
RULE_NO_LOCAL_NO_REASON = "no_local_no_reason"  # локального нет, причины тоже

RULE_TEXT = {
    RULE_HIGH_STAKES: "при цене ошибки >= high_stakes_cost поднятие разрешено "
                      "на пороге high_stakes_uncertainty",
    RULE_UNCERTAINTY: "обычная задача: поднятие при неопределённости >= uncertainty",
    RULE_LOCAL_FIRST: "неопределённость ниже порога — исполняет локальная модель",
    RULE_NO_LOCAL_NO_REASON: "локального исполнителя нет, а измеримой причины для "
                             "облака нет — отказ, а не тихое поднятие",
}

# сколько последних решений читаем из ленты для статистики и /decisions
JOURNAL_SCAN = 500

router = APIRouter()


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes")


# ------------------------------------------------------------------ типы

@dataclass(frozen=True, slots=True)
class Thresholds:
    """Пороги решения. Хранятся как данные (settings), а не как константы кода."""
    uncertainty: float = 0.60          # обычная задача: выше — облако
    high_stakes_uncertainty: float = 0.30   # дорогая ошибка: планка ниже
    high_stakes_cost: float = 0.70     # с какой цены ошибки задача «дорогая»

    def __post_init__(self) -> None:
        for name in ("uncertainty", "high_stakes_uncertainty", "high_stakes_cost"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{name}: ожидается число 0..1")
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name}: {value} вне диапазона 0..1")
        # Смысл правила «дорогая ошибка» — ПОНИЗИТЬ планку. Порог выше обычного
        # молча превратил бы дорогую задачу в более терпимую к локальным ошибкам.
        if self.high_stakes_uncertainty > self.uncertainty:
            raise ValueError("high_stakes_uncertainty должен быть не выше uncertainty: "
                             "дорогая ошибка понижает планку поднятия, а не повышает")


@dataclass(frozen=True, slots=True)
class DecisionRequest:
    """Описание работы на входе решения. Ничего не выдумываем: оценку
    неопределённости даёт вызывающий (классификатор/эвристика), здесь она —
    вход, а не догадка."""
    kind: str
    uncertainty: float
    error_cost: float = 0.0
    local_available: bool = True
    task_id: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("kind: вид работы обязателен")
        for name in ("uncertainty", "error_cost"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{name}: ожидается число 0..1")
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name}: {value} вне диапазона 0..1")


@dataclass(frozen=True, slots=True)
class Decision:
    """Вердикт с ОБЯЗАТЕЛЬНОЙ причиной и сработавшим порогом.

    Пустая причина запрещена конструктивно: решение без объяснения нельзя
    разобрать постфактум, а весь смысл модуля — чтобы поднятия можно было
    разобрать.
    """
    verdict: str
    reason: str
    rule: str
    threshold: float
    kind: str
    uncertainty: float
    error_cost: float
    local_available: bool
    task_id: int | None = None

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"verdict должен быть одним из {VERDICTS}, получено {self.verdict!r}")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason обязателен: вердикт без причины создать нельзя")
        if self.rule not in RULE_TEXT:
            raise ValueError(f"rule должен быть одним из {tuple(RULE_TEXT)}, "
                             f"получено {self.rule!r}")

    def as_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------ чистая логика

def effective_threshold(request: DecisionRequest, thresholds: Thresholds) -> tuple[str, float]:
    """Какой порог применим к этой задаче и по какому правилу.

    Правило «дорогая ошибка» живёт здесь целиком и в одном месте: при цене
    ошибки не ниже high_stakes_cost планка поднятия опускается до
    high_stakes_uncertainty. Отдельная функция — чтобы правило можно было
    проверить тестом напрямую, а не выводить из поведения decide().
    """
    if request.error_cost >= thresholds.high_stakes_cost:
        return RULE_HIGH_STAKES, thresholds.high_stakes_uncertainty
    return RULE_UNCERTAINTY, thresholds.uncertainty


def decide(request: DecisionRequest, thresholds: Thresholds) -> Decision:
    """Чистая функция решения: local | cloud | refuse. Ни БД, ни сети, ни моделей."""
    rule, threshold = effective_threshold(request, thresholds)
    common = {"kind": request.kind, "uncertainty": request.uncertainty,
              "error_cost": request.error_cost, "local_available": request.local_available,
              "task_id": request.task_id, "threshold": threshold}
    measured = (f"неопределённость {request.uncertainty:.2f} "
                f"{{cmp}} порога {threshold:.2f} (цена ошибки {request.error_cost:.2f})")

    if request.uncertainty >= threshold:
        # Поднятие всегда имеет измеримую причину — иначе это не апелляция.
        reason = measured.format(cmp=">=")
        if rule == RULE_HIGH_STAKES:
            reason += f"; порог понижен: цена ошибки >= {thresholds.high_stakes_cost:.2f}"
        if not request.local_available:
            reason += "; локальный исполнитель недоступен"
        return Decision(verdict="cloud", reason=reason, rule=rule, **common)

    if not request.local_available:
        # Недоступность локального исполнителя сама по себе НЕ причина для
        # облака: иначе любая поломка локального стека тихо переводила бы
        # работу на платный путь.
        reason = ("локальный исполнитель недоступен, измеримой причины для облака нет: "
                  + measured.format(cmp="<"))
        return Decision(verdict="refuse", reason=reason, rule=RULE_NO_LOCAL_NO_REASON, **common)

    reason = "исполняет локальная модель: " + measured.format(cmp="<")
    return Decision(verdict="local", reason=reason, rule=RULE_LOCAL_FIRST, **common)


# ------------------------------------------------------------------ пороги в БД

async def load_thresholds(svc) -> Thresholds:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == THRESHOLDS_KEY))).first()
    if row and row[0]:
        try:
            data = json.loads(svc.vault.decrypt(row[0]))
            return Thresholds(**{k: float(v) for k, v in data.items()
                                 if k in Thresholds.__slots__})
        except Exception:
            pass                       # испорченная запись не должна ронять решение
    return Thresholds()


async def save_thresholds(svc, thresholds: Thresholds) -> None:
    enc = svc.vault.encrypt(json.dumps(asdict(thresholds)))
    async with svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == THRESHOLDS_KEY))
        await s.execute(sa.insert(settings_kv).values(key=THRESHOLDS_KEY, value_enc=enc))
        await s.commit()


# ------------------------------------------------------------------ журнал

async def journal(svc, limit: int = JOURNAL_SCAN) -> list[dict]:
    """Последние решения из ленты событий (своей таблицы модуль не заводит).

    Источник — тот же, что и получатель события: шина. Значит в отчёте видно
    ровно то, что было записано, без второго хранилища, которое может разойтись.
    """
    async with svc.db.session() as s:
        rows = (await s.execute(
            sa.select(events_t).where(events_t.c.kind == EVENT_KIND)
            .order_by(events_t.c.id.desc()).limit(max(1, limit)))).fetchall()
    out: list[dict] = []
    for row in rows_dicts(rows):
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        ts = row.get("ts")
        out.append({**data, "ts": ts.isoformat() if hasattr(ts, "isoformat") else ts})
    return out


def summarize(decisions: list[dict]) -> dict:
    """Статистика: сколько ушло локально, сколько поднято и по каким причинам.

    Разбивка по правилам и по видам работ — это и есть ответ на вопрос, на чём
    локальная модель проседает: чаще всего поднимаются задачи какого вида.
    """
    verdicts = {v: 0 for v in VERDICTS}
    by_rule: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for d in decisions:
        verdict = d.get("verdict")
        if verdict in verdicts:
            verdicts[verdict] += 1
        if verdict != "cloud":
            continue
        rule = str(d.get("rule") or "")
        by_rule[rule] = by_rule.get(rule, 0) + 1
        kind = str(d.get("kind") or "")
        by_kind[kind] = by_kind.get(kind, 0) + 1

    def top(counts: dict[str, int], key: str) -> list[dict]:
        return [{key: name, "count": n}
                for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    return {"total": len(decisions), **verdicts,
            "escalation_reasons": top(by_rule, "rule"),
            "escalation_kinds": top(by_kind, "kind")}


# ------------------------------------------------------------------ ручки

class DecideIn(BaseModel):
    kind: str
    uncertainty: float
    error_cost: float = 0.0
    local_available: bool = True
    task_id: int | None = None


class ThresholdsIn(BaseModel):
    uncertainty: float | None = None
    high_stakes_uncertainty: float | None = None
    high_stakes_cost: float | None = None


def _require_flag() -> None:
    if not enabled():
        raise HTTPException(409, {"message": "local-first выключен", "flag": FLAG})


@router.get("/local-first")
async def state(request: Request):
    """Текущие пороги, правила и статистика. Читать можно и при выключенном
    флаге — тогда честно сказано enabled=false."""
    svc = request.app.state.svc
    thresholds = await load_thresholds(svc)
    return {"enabled": enabled(), "thresholds": asdict(thresholds), "rules": RULE_TEXT,
            "stats": summarize(await journal(svc))}


@router.get("/local-first/decisions")
async def decisions(request: Request, limit: int = 50):
    svc = request.app.state.svc
    limit = max(1, min(int(limit), JOURNAL_SCAN))
    return {"enabled": enabled(), "decisions": (await journal(svc, JOURNAL_SCAN))[:limit]}


@router.post("/local-first/decide")
async def decide_endpoint(request: Request, body: DecideIn):
    """Вынести решение и записать его в журнал. Меняет состояние (пишет
    событие), поэтому при выключенном флаге отказ — решение не выносится совсем."""
    _require_flag()
    svc = request.app.state.svc
    try:
        req = DecisionRequest(kind=body.kind, uncertainty=body.uncertainty,
                              error_cost=body.error_cost,
                              local_available=body.local_available, task_id=body.task_id)
    except ValueError as exc:
        raise HTTPException(400, {"message": str(exc)}) from exc
    decision = decide(req, await load_thresholds(svc))
    await svc.bus.emit(EVENT_KIND, **decision.as_dict())
    return {"decision": decision.as_dict()}


@router.post("/local-first/thresholds")
async def set_thresholds(request: Request, body: ThresholdsIn):
    _require_flag()
    svc = request.app.state.svc
    current = await load_thresholds(svc)
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        updated = replace(current, **patch)
    except ValueError as exc:
        raise HTTPException(400, {"message": str(exc)}) from exc
    await save_thresholds(svc, updated)
    await svc.bus.emit("local_first.thresholds_changed", **asdict(updated))
    return {"enabled": True, "thresholds": asdict(updated)}


FEATURE = Feature(name="local_first", router=router)
