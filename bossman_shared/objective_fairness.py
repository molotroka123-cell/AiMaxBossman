"""Кто из нескольких готовых целей идёт следующей — и почему именно она.

Допуск (`objective_admission`) сознательно рассматривает ОДНО предложение: он
отвечает «можно ли этой цели действовать сейчас», а не «чья очередь». Пока
целей две-три, разницы нет. На двадцати она решающая: без очерёдности порядок
задаёт тот, кто первым попал в цикл, и цель с непрерывным потоком поводов
забирает общий ключ конфликта снова и снова, а соседняя ждёт вечно.

Модуль ЧИСТЫЙ: никаких часов, никакой случайности, никакого ввода-вывода.
Время входит одним параметром `now`, факты о цели — записями `Candidate`,
которые собирает вызывающий (`ObjectiveStore.last_admission_at`,
`admissions_since`). Одинаковые входы дают одинаковый порядок в любом процессе
и после любого рестарта — иначе «справедливость» нельзя было бы ни проверить,
ни воспроизвести в отчёте.

Гарантия отсутствия голодания даётся НЕ старением. Старение ограничено (в этом
и смысл слова «bounded»), поэтому разрыв приоритетов в 100 единиц оно не
перекроет и перекрывать не должно. Голодание закрывают два других механизма, и
они здесь названы честно:

  * КВОТА В ОКНЕ — цель, выбравшая свои допуски за окно, становится
    неподходящей до конца окна, и очередь достаётся кому-то ещё. Это и есть
    гарантия: поток высокого приоритета конечен внутри окна.
  * КРАЙНИЙ СРОК ГОЛОДАНИЯ — цель, прождавшая дольше срока, поднимается выше
    всех неголодающих независимо от приоритета. Это ограниченный подъём (флаг,
    а не бесконечно растущее число), и он даёт верхнюю границу ожидания.

Старение — сглаживание ВНУТРИ окна: оно сокращает задержку близких по
приоритету целей, не переворачивая шкалу.

Направление приоритета: БОЛЬШЕ ЧИСЛО — ВАЖНЕЕ. В `bossman_v3/fleet/store.py`
для рабочих элементов флота принято ОБРАТНОЕ (`ORDER BY priority ASC`). Это
разные шкалы разных подсистем, и смешивать их нельзя; здесь она объявлена явно,
потому что в `objective_spec` поле приоритета до сих пор только валидировалось.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Причины, по которым кандидат не участвует в этом круге. Кандидат никогда не
# исчезает молча: владелец должен видеть, ПОЧЕМУ цель не идёт.
COOLDOWN_ACTIVE = "cooldown_active"
QUOTA_EXHAUSTED = "quota_exhausted"
NOT_READY = "not_ready"


class FairnessError(ValueError):
    """Расписание отказалось от входных данных; порядок не определён."""


def _finite(value: Any, name: str) -> float:
    if type(value) not in (int, float) or value != value or value in (float("inf"), float("-inf")):
        raise FairnessError(f"{name} must be a finite number")
    return float(value)


@dataclass(frozen=True, slots=True)
class FairnessPolicy:
    """Настройки очерёдности. Все — величины, а не намерения.

    `window_seconds`/`quota_per_window` задают гарантию: ни одна цель не может
    занять больше `quota_per_window` допусков за окно. `starvation_deadline_s`
    задаёт верхнюю границу ожидания подходящей цели. `aging_per_second` и
    `max_aging_bonus` — сглаживание внутри окна, и второе ОБЯЗАНО быть конечным.
    """
    window_seconds: float = 3600.0
    quota_per_window: int = 3
    starvation_deadline_s: float = 900.0
    aging_per_second: float = 1.0 / 60.0
    max_aging_bonus: float = 5.0

    def __post_init__(self) -> None:
        for name in ("window_seconds", "starvation_deadline_s", "aging_per_second"):
            if _finite(getattr(self, name), name) < 0:
                raise FairnessError(f"{name} must not be negative")
        if type(self.quota_per_window) is not int or self.quota_per_window < 0:
            raise FairnessError("quota_per_window must be a nonnegative integer")
        if _finite(self.max_aging_bonus, "max_aging_bonus") < 0:
            raise FairnessError("max_aging_bonus must not be negative")


@dataclass(frozen=True, slots=True)
class Candidate:
    """Факты об одной цели на момент `now`. Собираются вызывающим, не угадываются."""
    objective_id: str
    priority: int
    eligible_since: float
    last_admitted_at: float | None = None
    admissions_in_window: int = 0
    cooldown_until: float | None = None
    ready: bool = True

    def __post_init__(self) -> None:
        if not self.objective_id:
            raise FairnessError("objective_id is required")
        if type(self.priority) is not int:
            raise FairnessError("priority must be an integer")
        _finite(self.eligible_since, "eligible_since")
        for name in ("last_admitted_at", "cooldown_until"):
            value = getattr(self, name)
            if value is not None:
                _finite(value, name)


@dataclass(frozen=True, slots=True)
class Ranked:
    """Место кандидата и полный разбор того, как оно получилось."""
    objective_id: str
    eligible: bool
    reason: str
    priority: int
    waited_s: float
    aging_bonus: float
    effective_priority: float
    starved: bool
    facts: dict[str, Any] = field(default_factory=dict)


def _ineligible(candidate: Candidate, now: float, policy: FairnessPolicy) -> str | None:
    if not candidate.ready:
        return NOT_READY
    # Цель, которая станет подходящей только в будущем, не «ждёт ноль секунд» —
    # она вообще не в этом круге. Иначе её ожидание зажималось бы в ноль, разность
    # эффективных приоритетов переставала быть монотонной, и пара переворачивалась
    # лишний раз на ровном месте.
    if now < candidate.eligible_since:
        return NOT_READY
    if candidate.cooldown_until is not None and now < candidate.cooldown_until:
        return COOLDOWN_ACTIVE
    # Квота — это гарантия против голодания, а не удобство: цель, выбравшая
    # окно, уходит с дороги, даже если её приоритет выше всех остальных.
    if policy.quota_per_window and candidate.admissions_in_window >= policy.quota_per_window:
        return QUOTA_EXHAUSTED
    return None


def rank(candidates, *, now: float, policy: FairnessPolicy | None = None) -> tuple[Ranked, ...]:
    """Полный детерминированный порядок кандидатов и разбор по каждому.

    Возвращаются ВСЕ кандидаты, подходящие первыми: неподходящий не удаляется
    из отчёта, у него проставлена причина. Порядок среди подходящих:

      1. голодающие (ждут дольше крайнего срока) — раньше всех остальных;
      2. выше эффективный приоритет (приоритет + ограниченное старение);
      3. дольше ждёт;
      4. `objective_id` — чтобы порядок был полным и повторяемым.
    """
    policy = policy or FairnessPolicy()
    now = _finite(now, "now")
    seen: set[str] = set()
    rows: list[Ranked] = []
    for candidate in candidates:
        if candidate.objective_id in seen:
            raise FairnessError(f"duplicate candidate: {candidate.objective_id}")
        seen.add(candidate.objective_id)
        # Ожидание считается от последнего ОБСЛУЖИВАНИЯ, если оно было: цель,
        # только что отработавшую, нельзя считать ждущей с начала времён.
        since = candidate.eligible_since
        if candidate.last_admitted_at is not None:
            since = max(since, candidate.last_admitted_at)
        waited = max(0.0, now - since)
        # Надбавка НЕПРЕРЫВНА, а не ступенчата, и это не косметика. Со
        # ступенькой `int(waited * rate)` две цели с разным началом ожидания
        # переступают в разных фазах, и их взаимный порядок меняется каждые
        # полминуты — ровно та осцилляция, которую N4 запрещает; свойство ниже
        # (test_the_order_of_any_pair_flips_at_most_once) её и поймало.
        #
        # С непрерывной надбавкой разность эффективных приоритетов пары ведёт
        # себя так: пока обе не упёрлись в потолок, она ПОСТОЯННА (ждут-то они
        # с одинаковой скоростью); затем та, что ждёт дольше, упирается первой,
        # и разность монотонно идёт в одну сторону; после второго потолка снова
        # постоянна. Монотонная величина меняет знак не больше одного раза —
        # значит взаимный порядок пары переворачивается максимум однажды.
        bonus = min(float(policy.max_aging_bonus), waited * policy.aging_per_second)
        reason = _ineligible(candidate, now, policy)
        starved = (reason is None and policy.starvation_deadline_s > 0
                   and waited >= policy.starvation_deadline_s)
        rows.append(Ranked(
            objective_id=candidate.objective_id, eligible=reason is None,
            reason=reason or "eligible", priority=candidate.priority, waited_s=waited,
            aging_bonus=bonus, effective_priority=candidate.priority + bonus, starved=starved,
            facts={"admissions_in_window": candidate.admissions_in_window,
                   "quota_per_window": policy.quota_per_window,
                   "last_admitted_at": candidate.last_admitted_at,
                   "cooldown_until": candidate.cooldown_until}))
    # Внутри голодающих порядок — ПО ДЛИТЕЛЬНОСТИ ОЖИДАНИЯ, а не по приоритету.
    # Иначе, когда голодать начинает вторая цель пары, приоритет снова берёт
    # верх и порядок возвращается назад: пара переворачивается дважды подряд.
    # По длительности ожидания тот, кто вошёл в голодание раньше, остаётся
    # впереди навсегда — подъём случается один раз и не откатывается.
    rows.sort(key=lambda r: (not r.eligible, not r.starved,
                             -r.waited_s if r.starved else -r.effective_priority,
                             -r.waited_s, r.objective_id))
    return tuple(rows)


def select(candidates, *, now: float, policy: FairnessPolicy | None = None) -> Ranked | None:
    """Единственный победитель круга, или None если подходящих нет."""
    ordered = rank(candidates, now=now, policy=policy)
    return ordered[0] if ordered and ordered[0].eligible else None


def candidate_from_store(store, objective_id: str, *, priority: int, eligible_since: float,
                         now: float, policy: FairnessPolicy | None = None,
                         cooldown_until: float | None = None, ready: bool = True) -> Candidate:
    """Собрать кандидата из ДОЛГОВЕЧНОЙ истории допусков, а не из предположений.

    Считает `admissions_since` по окну и берёт `last_admission_at` — то есть
    обслуживание, а не предложение.
    """
    policy = policy or FairnessPolicy()
    window_start = _finite(now, "now") - policy.window_seconds
    return Candidate(
        objective_id=objective_id, priority=priority, eligible_since=eligible_since,
        last_admitted_at=store.last_admission_at(objective_id),
        admissions_in_window=store.admissions_since(objective_id, window_start),
        cooldown_until=cooldown_until, ready=ready)
