"""«Паспорт факта» — происхождение обязательно для любой цифры интерфейса.

Замысел взят у обучающего корпуса (`learning/trace.py`, `evidence_records`):
там запись со статусом VERIFIED невозможна без источника и времени наблюдения.
Цифры Главной, бенчмарка и панелей до сих пор жили без этого — число приходило
в UI голым, и через минуту уже нельзя было сказать, откуда оно и насколько
свежее. Этот слой переносит то же требование на показатели.

Устройство:

  * `Fact` — значение вместе с паспортом. Конструктор ОТКАЗЫВАЕТ (ValueError),
    если нет `source`, `method` или `observed_at`. Это главное свойство слоя:
    факт без происхождения не существует как объект, а не «создаётся с
    предупреждением в лог». Предупреждение можно не заметить, исключение —
    нельзя;
  * реестр производителей модульного уровня: `register(key, producer)`. Любой
    другой код (панель, бенчмарк, экспорт) регистрирует функцию, которая умеет
    посчитать показатель ВМЕСТЕ с паспортом — вернуть голое число попросту
    некуда. Повторная регистрация того же ключа заменяет производителя, чтобы
    перезагрузка модуля не давала дублей;
  * производные показатели несут `computed_from` — ключи, из которых выведены.
    Каждый такой ключ сам разрешим в реестре, поэтому цепочка происхождения
    прослеживается глубже одного шага, а не обрывается на слове «расчёт».

Свои производители (`tasks.total`, `tasks.completed`, `runs.last_24h`,
`approvals.pending`, производный `tasks.completion_ratio`) считают по живым
таблицам `bcc/db.py` — слой поставляется не пустым, иначе требование
«происхождение обязательно» осталось бы декларацией.

Флаг `BOSSMAN_PROVENANCE_ENABLED` по умолчанию выключен. Обе ручки читающие и
ничего не меняют, поэтому отвечают всегда, но отчёт честно показывает
`enabled: false` — включённость слоя видна тому, кто читает цифры.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import approvals as approvals_t, task_runs as runs_t, tasks as tasks_t, utcnow
from . import Feature

FLAG = "BOSSMAN_PROVENANCE_ENABLED"
router = APIRouter()

# Как получено значение. Список закрыт намеренно: «прочее» превращает паспорт
# в свободный текст, по которому нельзя судить о доверии к цифре.
METHODS = ("db_query", "computed", "event", "config")


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes")


class ProvenanceError(ValueError):
    """Отказ в конструкции факта: паспорт неполон или противоречив."""


class UnknownFactKey(KeyError):
    """Ключ не зарегистрирован ни одним производителем."""


@dataclass(frozen=True)
class Fact:
    """Значение показателя и его паспорт. Неполный паспорт = отказ, не warning."""

    value: Any
    key: str
    source: str = ""
    method: str = ""
    observed_at: datetime | None = None
    computed_from: tuple[str, ...] = ()
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not str(self.key or "").strip():
            raise ProvenanceError("факт без key: показатель обязан быть назван")
        if not str(self.source or "").strip():
            raise ProvenanceError(f"факт {self.key!r} без source: откуда взято значение — неизвестно")
        if str(self.method or "").strip() not in METHODS:
            raise ProvenanceError(
                f"факт {self.key!r}: method должен быть одним из {METHODS}, получено {self.method!r}")
        if not isinstance(self.observed_at, datetime):
            raise ProvenanceError(
                f"факт {self.key!r} без observed_at: цифра без времени наблюдения неотличима от устаревшей")
        # computed_from нормализуем к кортежу строк — паспорт неизменяем вместе с фактом
        cf = tuple(str(k).strip() for k in (self.computed_from or ()))
        if any(not k for k in cf):
            raise ProvenanceError(f"факт {self.key!r}: пустой ключ в computed_from")
        object.__setattr__(self, "computed_from", cf)
        if self.method == "computed" and not cf:
            raise ProvenanceError(
                f"факт {self.key!r}: method=computed обязан назвать computed_from — "
                "иначе цепочка происхождения обрывается на первом шаге")
        try:
            conf = float(self.confidence)
        except (TypeError, ValueError):
            raise ProvenanceError(f"факт {self.key!r}: confidence не число") from None
        if not 0.0 <= conf <= 1.0:
            raise ProvenanceError(f"факт {self.key!r}: confidence вне 0..1 ({conf})")
        object.__setattr__(self, "confidence", conf)

    def as_dict(self) -> dict:
        return {"key": self.key, "value": self.value, "source": self.source,
                "method": self.method, "observed_at": self.observed_at.isoformat(),
                "computed_from": list(self.computed_from), "confidence": self.confidence}


Producer = Callable[[Any], Awaitable[Fact]]


@dataclass(frozen=True)
class Registration:
    key: str
    producer: Producer
    description: str = ""
    # Заявленные зависимости: список ключей видно в /provenance ДО вычисления,
    # чтобы читатель понимал цену показателя, не запуская его.
    depends_on: tuple[str, ...] = field(default=())


_PRODUCERS: dict[str, Registration] = {}


def register(key: str, producer: Producer, *, description: str = "",
             depends_on: tuple[str, ...] | list[str] = ()) -> Registration:
    """Регистрирует производителя показателя. Идемпотентно: тот же ключ заменяет
    прежнюю запись, поэтому повторный импорт модуля не плодит дубли."""
    key = str(key or "").strip()
    if not key:
        raise ProvenanceError("register: пустой ключ")
    if not callable(producer):
        raise ProvenanceError(f"register({key!r}): producer не вызываем")
    reg = Registration(key=key, producer=producer, description=description,
                       depends_on=tuple(depends_on))
    _PRODUCERS[key] = reg
    return reg


def unregister(key: str) -> bool:
    return _PRODUCERS.pop(key, None) is not None


def known_keys() -> list[str]:
    return sorted(_PRODUCERS)


def catalog() -> list[dict]:
    """Описание известных ключей БЕЗ вычисления значений: список ручки
    /provenance не должен стоить обхода базы."""
    return [{"key": r.key, "description": r.description, "depends_on": list(r.depends_on)}
            for r in (_PRODUCERS[k] for k in known_keys())]


async def resolve(key: str, svc: Any) -> Fact:
    """Считает показатель через его производителя. Неизвестный ключ — отказ:
    молча вернуть None значило бы отдать в UI цифру без происхождения."""
    reg = _PRODUCERS.get(str(key or "").strip())
    if reg is None:
        raise UnknownFactKey(key)
    fact = await reg.producer(svc)
    if not isinstance(fact, Fact):
        raise ProvenanceError(f"производитель ключа {key!r} вернул не Fact — паспорт потерян")
    return fact


# ---------- собственные производители: счёт по живым таблицам ----------

async def _scalar(svc: Any, query) -> int:
    async with svc.db.session() as s:
        return int((await s.execute(query)).scalar_one() or 0)


async def _tasks_total(svc: Any) -> Fact:
    value = await _scalar(svc, sa.select(sa.func.count()).select_from(tasks_t))
    return Fact(value=value, key="tasks.total", source="table:tasks", method="db_query",
                observed_at=utcnow())


async def _tasks_completed(svc: Any) -> Fact:
    value = await _scalar(svc, sa.select(sa.func.count()).select_from(tasks_t)
                          .where(tasks_t.c.status == "completed"))
    return Fact(value=value, key="tasks.completed",
                source="table:tasks WHERE status='completed'", method="db_query",
                observed_at=utcnow())


async def _runs_last_24h(svc: Any) -> Fact:
    """Запуски за сутки. Окно попадает в source: та же цифра завтра означает
    другое множество строк, и по паспорту это должно быть видно."""
    now = utcnow()
    cutoff = now - timedelta(hours=24)
    value = await _scalar(svc, sa.select(sa.func.count()).select_from(runs_t)
                          .where(runs_t.c.started_at.is_not(None), runs_t.c.started_at >= cutoff))
    return Fact(value=value, key="runs.last_24h",
                source=f"table:task_runs WHERE started_at >= {cutoff.isoformat()}",
                method="db_query", observed_at=now)


async def _approvals_pending(svc: Any) -> Fact:
    value = await _scalar(svc, sa.select(sa.func.count()).select_from(approvals_t)
                          .where(approvals_t.c.status == "pending"))
    return Fact(value=value, key="approvals.pending",
                source="table:approvals WHERE status='pending'", method="db_query",
                observed_at=utcnow())


async def _tasks_completion_ratio(svc: Any) -> Fact:
    """Производный показатель: доля завершённых задач.

    Считается не своим запросом, а из двух зарегистрированных фактов — тогда
    `computed_from` называет ключи, которые сами разрешимы, и читатель может
    спуститься к их паспортам. Уверенность — минимум из уверенностей слагаемых:
    производное не может быть надёжнее худшего из своих источников.
    """
    total = await resolve("tasks.total", svc)
    done = await resolve("tasks.completed", svc)
    value = round(done.value / total.value, 6) if total.value else 0.0
    return Fact(value=value, key="tasks.completion_ratio",
                source="derived: tasks.completed / tasks.total",
                method="computed",
                # позднее из двух наблюдений: производное не свежее своего сырья
                observed_at=max(total.observed_at, done.observed_at),
                computed_from=("tasks.completed", "tasks.total"),
                confidence=min(total.confidence, done.confidence))


register("tasks.total", _tasks_total, description="всего задач в системе")
register("tasks.completed", _tasks_completed, description="задач со статусом completed")
register("runs.last_24h", _runs_last_24h, description="запусков задач за последние 24 часа")
register("approvals.pending", _approvals_pending, description="ожидающих подтверждений")
register("tasks.completion_ratio", _tasks_completion_ratio,
         description="доля завершённых задач (производный)",
         depends_on=("tasks.completed", "tasks.total"))


# ---------- ручки (обе читающие) ----------

@router.get("/provenance")
async def list_facts():
    """Каталог известных показателей без их вычисления."""
    return {"enabled": enabled(), "count": len(_PRODUCERS), "methods": list(METHODS),
            "keys": catalog()}


@router.get("/provenance/{key}")
async def get_fact(key: str, request: Request):
    """Значение показателя вместе с паспортом."""
    try:
        fact = await resolve(key, request.app.state.svc)
    except UnknownFactKey:
        raise HTTPException(404, {"message": f"показатель {key!r} не зарегистрирован",
                                  "known": known_keys()}) from None
    return {"enabled": enabled(), **fact.as_dict()}


FEATURE = Feature(name="provenance", router=router)
