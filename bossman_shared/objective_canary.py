"""Канареечная активация ревизии: сперва малая когорта, потом все.

`prepare_rollback` уже отдаёт ПОРЯДОК отката как данные, но канареечного шага
не было вовсе: новая ревизия либо действовала для всех, либо ни для кого. Из-за
этого в карточке V5 «Canary admission and a live rollback rehearsal» стояло
NOT_RUN, а ошибка ревизии обнаруживалась сразу на всём парке целей.

Четыре правила, и все четыре — fail-closed.

  КОГОРТА ВЫБИРАЕТСЯ ДЕТЕРМИНИРОВАННО. sha256 по цифре ревизии и идентификатору
  цели, никакого генератора случайных чисел: тот же выпуск обязан дать ту же
  когорту в другом процессе, иначе «канарейка» невоспроизводима, а её отчёт
  непроверяем. Когорта ограничена и снизу, и сверху: одна цель ничего не
  показывает, а «канарейка» на девяноста процентах парка — это не канарейка.

  МОЛЧАНИЕ — НЕ УСПЕХ. Пока не отчитался КАЖДЫЙ член когорты, вердикт PENDING, а
  не PASSED. Отсутствие плохих новостей не является хорошей новостью: именно так
  зависший канареечный прогон превращается в общий выпуск.

  ОДНОГО ПАДЕНИЯ ДОСТАТОЧНО. Любой нездоровый член — FAILED, и широкая
  активация закрыта до отката или новой ревизии. Не «доля здоровых»: если
  ревизия ломает одну цель из десяти, она ломает цель.

  УЛИКА ЗДОРОВЬЯ ОБЯЗАНА РАЗРЕШАТЬСЯ И БЫТЬ ПРИВЯЗАННОЙ (IV5-CAN-002). Раньше
  `evidence_ref` был просто строкой: её никто не резолвил и не проверял, поэтому
  ЛЮБАЯ строка — выдуманная, чужая, позапрошлогодняя — открывала широкий выпуск,
  а пустая строка по умолчанию была ровно такой же строкой. Проверка «строка
  непустая» это не чинит: `"x"` непустая. Здесь улика — не строка, а
  РАЗРЕШАЕМАЯ ССЫЛКА: резолвер обязан вернуть `CanaryAttestation`, привязанный к
  цели, к цифре ревизии, к составу когорты, к ЭТОМУ прогону и к процессу,
  который его начал, и выпущенный внутри окна прогона. Ссылка, которую резолвер
  не разрешил, ОТКАЗЫВАЕТСЯ, а не считается «улики нет, значит и претензий нет».

  Улика нужна, чтобы ОТКРЫТЬ выпуск, а не чтобы его закрыть: отчёт `healthy=False`
  и молчание принимаются без улики. Иначе злоумышленнику достаточно испортить
  свою же улику, чтобы падение перестало считаться падением.

Модуль ЧИСТЫЙ: ни ввода-вывода, ни часов, ни случайности; время входит `now`,
ключ подписи улик входит аргументом. Здесь ничего не активируется и не
откатывается — это решение, а не действие.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

PENDING = "PENDING"
PASSED = "PASSED"
FAILED = "FAILED"

# Меньше трёх целей — не наблюдение, а совпадение.
MIN_COHORT = 3

# Версионированный префикс ссылки на улику. Формат меняется — префикс меняется,
# и старые ссылки перестают разрешаться, а не «разрешаются как-нибудь».
EVIDENCE_SCHEME = "cev1"

# Почему улика не принята. Строки машиночитаемы: они попадают в вердикт.
UNRESOLVED = "unresolved"
WRONG_OBJECTIVE = "wrong_objective"
WRONG_REVISION = "wrong_revision"
WRONG_COHORT = "wrong_cohort"
WRONG_RUN = "wrong_run"
WRONG_PROCESS = "wrong_process"
STALE = "stale"


class CanaryError(ValueError):
    """Канареечный план отказался от входных данных; вердикта нет."""


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class CanaryPolicy:
    fraction: float = 0.2
    min_cohort: int = MIN_COHORT
    max_cohort: int = 25

    def __post_init__(self) -> None:
        if not (0.0 < self.fraction < 1.0):
            raise CanaryError("fraction must be strictly between 0 and 1")
        if type(self.min_cohort) is not int or self.min_cohort < MIN_COHORT:
            raise CanaryError(f"min_cohort must be an integer >= {MIN_COHORT}")
        if type(self.max_cohort) is not int or self.max_cohort < self.min_cohort:
            raise CanaryError("max_cohort must be an integer >= min_cohort")


@dataclass(frozen=True, slots=True)
class CanaryPlan:
    """Кто именно получает новую ревизию первым. Построение ничего не активирует.

    `cohort_digest` и `run_id` — не украшение: улика привязывается к ним, поэтому
    улика прошлого прогона той же ревизии на том же парке не подойдёт этому.
    """
    revision_digest: str
    cohort: tuple[str, ...]
    population: tuple[str, ...]
    started_at: float
    process_identity: str = ""
    cohort_digest: str = ""
    run_id: str = ""

    @property
    def rest(self) -> tuple[str, ...]:
        held = set(self.cohort)
        return tuple(o for o in self.population if o not in held)


@dataclass(frozen=True, slots=True)
class CanaryAttestation:
    """Что улика УТВЕРЖДАЕТ. Резолвер возвращает это, а не строку.

    Каждое поле — привязка. Отсутствие привязки — дыра: улика без `run_id`
    переживает прогон, улика без `objective_id` переносится на соседнюю цель.
    """
    evidence_ref: str
    objective_id: str
    revision_digest: str
    cohort_digest: str
    run_id: str
    issued_at: float
    process_identity: str = ""


@dataclass(frozen=True, slots=True)
class CanaryOutcome:
    """Отчёт одного члена когорты. `healthy=None` означает «ещё не известно».

    `evidence_ref` — ССЫЛКА, а не утверждение: сама по себе она ничего не
    доказывает и ничего не открывает. Значение по умолчанию `""` не «улики нет,
    и ладно», а ссылка, которую резолвер не разрешит, — то есть отказ для любой
    заявки о здоровье. Для отчёта о падении и для молчания улика не требуется.
    """
    objective_id: str
    healthy: bool | None
    evidence_ref: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CanaryVerdict:
    state: str
    reason: str
    revision_digest: str
    reported: tuple[str, ...] = ()
    unhealthy: tuple[str, ...] = ()
    silent: tuple[str, ...] = ()
    facts: dict[str, Any] = field(default_factory=dict)
    # Кто заявил здоровье уликой, которая не разрешилась или не привязана:
    # пары (objective_id, причина). Непустой кортеж закрывает широкий выпуск.
    unattested: tuple[tuple[str, str], ...] = ()
    # Вердикт вынесен ПРИ проверке улик. False означает «улики не проверялись»,
    # а не «улики в порядке»: такой вердикт не имеет права открыть выпуск.
    attested: bool = False

    @property
    def passed(self) -> bool:
        return self.state == PASSED


class CanaryEvidenceLedger:
    """Выдаёт и разрешает привязанные улики. Ключ приходит от вызывающего.

    Ссылка самодостаточна: `cev1.<payload>.<hmac>`. Подделать её, не имея ключа,
    нельзя — а значит «выдуманная строка» не разрешается ни в какой атрибут, и
    привязки не проверяются на данных, которые предъявитель выбрал сам.

    Модуль остаётся чистым: ни файла ключа, ни часов, ни случайности. Время
    выпуска улики передаёт тот, кто её выпускает.
    """

    __slots__ = ("_key",)

    def __init__(self, key: bytes) -> None:
        if not isinstance(key, (bytes, bytearray)) or len(key) < 16:
            raise CanaryError("an evidence key of at least 16 bytes is required")
        self._key = bytes(key)

    def _tag(self, body: bytes) -> str:
        return hmac.new(self._key, body, hashlib.sha256).hexdigest()

    def issue(self, plan: CanaryPlan, objective_id: str, *, issued_at: float) -> str:
        """Выпустить улику для члена когорты ЭТОГО прогона."""
        if not isinstance(plan, CanaryPlan):
            raise CanaryError("a canary plan is required")
        if objective_id not in plan.cohort:
            raise CanaryError(f"{objective_id} is not in the cohort")
        if type(issued_at) not in (int, float):
            raise CanaryError("issued_at must be a number")
        claim = {"objective_id": objective_id, "revision_digest": plan.revision_digest,
                 "cohort_digest": plan.cohort_digest, "run_id": plan.run_id,
                 "issued_at": float(issued_at), "process_identity": plan.process_identity}
        body = json.dumps(claim, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")
        payload = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
        return f"{EVIDENCE_SCHEME}.{payload}.{self._tag(payload.encode('ascii'))}"

    def resolve(self, evidence_ref: Any) -> CanaryAttestation | None:
        """Разрешить ссылку. Всё, что не проверяется ключом, — None, а не догадка."""
        if not isinstance(evidence_ref, str):
            return None
        parts = evidence_ref.split(".")
        if len(parts) != 3 or parts[0] != EVIDENCE_SCHEME:
            return None
        _, payload, tag = parts
        # compare_digest: без него время сравнения течёт по префиксу подписи.
        if not hmac.compare_digest(self._tag(payload.encode("ascii")), tag):
            return None
        try:
            body = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            claim = json.loads(body.decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return None
        if not isinstance(claim, dict):
            return None
        try:
            return CanaryAttestation(
                evidence_ref=evidence_ref,
                objective_id=str(claim["objective_id"]),
                revision_digest=str(claim["revision_digest"]),
                cohort_digest=str(claim["cohort_digest"]),
                run_id=str(claim["run_id"]),
                issued_at=float(claim["issued_at"]),
                process_identity=str(claim.get("process_identity", "")))
        except (KeyError, TypeError, ValueError):
            return None


def plan_canary(population: Iterable[str], *, revision_digest: str, now: float,
                policy: CanaryPolicy | None = None, process_identity: str = "",
                run_nonce: str = "") -> CanaryPlan:
    """Выбрать когорту детерминированно по цифре ревизии."""
    policy = policy or CanaryPolicy()
    if not isinstance(revision_digest, str) or not revision_digest:
        raise CanaryError("revision_digest is required")
    ids = list(population)
    if len(set(ids)) != len(ids):
        raise CanaryError("duplicate objective in the population")
    if len(ids) < policy.min_cohort:
        raise CanaryError(f"a population of {len(ids)} cannot host a canary "
                          f"of at least {policy.min_cohort}")
    ordered = sorted(ids, key=lambda oid: _digest({"r": revision_digest, "o": oid}))
    size = min(policy.max_cohort, max(policy.min_cohort, int(len(ordered) * policy.fraction)))
    cohort = tuple(sorted(ordered[:size]))
    cohort_digest = _digest({"r": revision_digest, "c": list(cohort)})
    # Прогон, а не только ревизия: `started_at` разводит два прогона одной
    # ревизии на одном парке, иначе улика вчерашнего прогона годится сегодня.
    run_id = _digest({"cd": cohort_digest, "s": float(now),
                      "p": process_identity, "n": run_nonce})
    return CanaryPlan(revision_digest, cohort, tuple(sorted(ids)), now,
                      process_identity, cohort_digest, run_id)


def _binding_failure(attestation: CanaryAttestation | None, plan: CanaryPlan,
                     objective_id: str, now: float) -> str:
    """Почему улика не годится ЭТОМУ члену ЭТОГО прогона. Пустая строка — годится."""
    if not isinstance(attestation, CanaryAttestation):
        # Резолвер не вернул улику. Это ОТКАЗ, а не «улики нет, значит и
        # претензий нет»: неразрешимая ссылка — самый дешёвый способ подделки.
        return UNRESOLVED
    if attestation.objective_id != objective_id:
        return WRONG_OBJECTIVE
    if attestation.revision_digest != plan.revision_digest:
        return WRONG_REVISION
    if attestation.cohort_digest != plan.cohort_digest:
        return WRONG_COHORT
    if attestation.run_id != plan.run_id:
        return WRONG_RUN
    if plan.process_identity and attestation.process_identity != plan.process_identity:
        return WRONG_PROCESS
    # Улика не может быть выпущена до начала прогона или в будущем.
    if not (plan.started_at <= attestation.issued_at <= now):
        return STALE
    return ""


def evaluate_canary(plan: CanaryPlan, outcomes: Iterable[CanaryOutcome],
                    *, resolve: Callable[[Any], CanaryAttestation | None] | None = None,
                    now: float | None = None) -> CanaryVerdict:
    """Вердикт по отчётам когорты. Молчание — PENDING, одно падение — FAILED.

    `resolve` разрешает улику здоровья. Без него вердикт помечается
    `attested=False` и НЕ имеет права открыть широкую активацию: см.
    `may_activate_broadly`.
    """
    if not isinstance(plan, CanaryPlan):
        raise CanaryError("a canary plan is required")
    if resolve is not None and now is None:
        # Без показания часов свежесть улики не судится. Молча принять улику
        # любого возраста — то же самое, что не проверять её вовсе.
        raise CanaryError("now is required to judge evidence freshness")
    rows: dict[str, CanaryOutcome] = {}
    for outcome in outcomes:
        if not isinstance(outcome, CanaryOutcome):
            raise CanaryError("outcomes must be CanaryOutcome")
        if outcome.objective_id not in plan.cohort:
            # Отчёт не от члена когорты — не улика об этой ревизии.
            raise CanaryError(f"{outcome.objective_id} is not in the cohort")
        if outcome.healthy is not None and type(outcome.healthy) is not bool:
            raise CanaryError("healthy must be bool or None")
        previous = rows.get(outcome.objective_id)
        # Failure is sticky within one revision/cohort. A later healthy report
        # cannot erase the failure or manufacture order-dependent eligibility.
        if previous is not None and previous.healthy is False:
            continue
        rows[outcome.objective_id] = outcome
    unhealthy = tuple(sorted(o for o, r in rows.items() if r.healthy is False))
    silent = tuple(sorted(o for o in plan.cohort if rows.get(o) is None
                          or rows[o].healthy is None))
    # Улику требуем только с заявки о ЗДОРОВЬЕ. Падение и молчание закрывают
    # выпуск и без улики — иначе испорченная улика отменяла бы падение.
    unattested: list[tuple[str, str]] = []
    if resolve is not None:
        for objective_id in sorted(o for o, r in rows.items() if r.healthy is True):
            try:
                attestation = resolve(rows[objective_id].evidence_ref)
            except Exception:  # noqa: BLE001 — резолвер упал: улики нет, fail-closed
                attestation = None
            failure = _binding_failure(attestation, plan, objective_id, float(now))
            if failure:
                unattested.append((objective_id, failure))
    facts = {"cohort": len(plan.cohort), "population": len(plan.population),
             "reported": len(rows), "attested": resolve is not None,
             "unattested": len(unattested)}
    common = {"revision_digest": plan.revision_digest, "reported": tuple(sorted(rows)),
              "silent": silent, "facts": facts, "unattested": tuple(unattested),
              "attested": resolve is not None}
    # Порядок важен: одно падение решает даже при неполном отчёте. Ждать
    # остальных, зная, что ревизия уже сломала цель, незачем.
    if unhealthy:
        return CanaryVerdict(FAILED, "canary_member_unhealthy", unhealthy=unhealthy, **common)
    if unattested:
        # Не PENDING: ждать нечего. Заявка о здоровье, которую нельзя разрешить,
        # не станет разрешимой от новых отчётов — прогон скомпрометирован.
        return CanaryVerdict(FAILED, "canary_evidence_unattested", **common)
    if silent:
        return CanaryVerdict(PENDING, "canary_incomplete", **common)
    return CanaryVerdict(PASSED, "canary_clear", **common)


def may_activate_broadly(verdict: CanaryVerdict) -> tuple[bool, str]:
    """Можно ли выпускать ревизию на остальной парк. (можно, причина)."""
    if not isinstance(verdict, CanaryVerdict):
        return False, "no_canary_verdict"
    if verdict.unattested:
        return False, "canary_evidence_unattested"
    if verdict.state == FAILED:
        return False, "canary_failed"
    if verdict.state != PASSED:
        return False, "canary_incomplete"
    if not verdict.attested:
        # Вердикт вынесен без проверки улик. «Улики не проверяли» — не «улики в
        # порядке»: непроверенное здоровье не открывает парк.
        return False, "canary_unattested"
    return True, "canary_passed"


def authorize_broad_activation(
        plan: CanaryPlan, outcomes: Iterable[CanaryOutcome], *,
        resolve: Callable[[Any], CanaryAttestation | None],
        now: float) -> tuple[bool, str, CanaryVerdict]:
    """ЕДИНАЯ дверь широкой активации: разрешение улик и вердикт неразделимы.

    `evaluate_canary` и `may_activate_broadly` можно вызвать порознь и забыть
    резолвер. Здесь забыть его нельзя — он обязателен. Настоящий вызывающий,
    который выпускает ревизию на весь парк, обязан идти через эту функцию.
    """
    if resolve is None:
        raise CanaryError("broad activation requires an evidence resolver")
    verdict = evaluate_canary(plan, outcomes, resolve=resolve, now=now)
    allowed, reason = may_activate_broadly(verdict)
    return allowed, reason, verdict
