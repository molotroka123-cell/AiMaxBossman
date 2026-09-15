"""Дверь широкой активации: производственный ВЫЗЫВАЮЩИЙ канареечного гейта.

`objective_canary` был верен и закрыт — план когорты, привязанная улика, вердикт
и единственная дверь `authorize_broad_activation`. Дефект P0-1 был не в
примитиве, а в том, что в продакшене его НИКТО не вызывал: гейт стоял, а живой
путь широкой активации шёл мимо. Гейт без вызывающего — не гейт.

Живой путь один, и он в Command Center: `bcc.v2.skill_evaluation._apply_promotion`
переключает `skills.current_version_id`, а колонки когорты в схеме нет — значит
кандидат достаётся ВСЕМУ парку сразу. Этот модуль — то, через что теперь обязан
пройти такой переход, и он же держит ДОЛГОВЕЧНУЮ часть решения.

Что здесь есть и чего нет:

  * есть привязка вердикта к КОНКРЕТНОЙ личности перехода — предмет (skill),
    ревизия (отпечаток версии), состав когорты, прогон и процесс;
  * есть долговечность: прогон, отчёты когорты и решение живут в
    `v5_canary_runs`/`v5_canary_reports` (`ObjectiveStore`), поэтому перезапуск
    не стирает ни падение, ни разрешение;
  * есть разрешение (`BroadActivationGrant`), подписанное ключом улик: обойти
    дверь и позвать применение напрямую можно, но без разрешения оно ОТКАЖЕТ;
  * НЕТ раската ревизий целей: производственного пути «revise на парк» в дереве
    не существует, и выдумывать его здесь только ради того, чтобы было что
    гейтить, — обман.

Fail-closed везде. Нет вердикта, нет улики, чужой процесс, прошлый прогон,
закрытый прогон, правленая строка прогона, просроченное разрешение — ОТКАЗ, а
не «претензий нет».
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .objective_canary import (CanaryEvidenceLedger, CanaryError, CanaryOutcome, CanaryPlan,
                               CanaryPolicy, CanaryVerdict, authorize_broad_activation,
                               plan_canary)
from .objective_store import ObjectiveStore, ObjectiveStoreError

OPEN = "OPEN"
ACTIVATED = "ACTIVATED"
ABANDONED = "ABANDONED"

# Разрешение живёт секунды: оно выдаётся ради ОДНОГО применения сразу после
# вердикта. Разрешение, годное час, — это вердикт, годный час.
GRANT_TTL_SECONDS = 30.0

REASON_UNKNOWN_RUN = "canary_run_unknown"
REASON_NOT_OPEN = "canary_run_not_open"
REASON_WRONG_PROCESS = "canary_wrong_process"
REASON_WRONG_OWNER = "canary_wrong_owner"
REASON_TAMPERED = "canary_run_tampered"
REASON_NO_GRANT = "broad_activation_grant_missing"
REASON_BAD_GRANT = "broad_activation_grant_invalid"
REASON_STALE_GRANT = "broad_activation_grant_stale"


class ActivationError(RuntimeError):
    """Широкая активация отказана или невозможна. Активации не произошло."""


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("utf-8")
    ).hexdigest()


def activation_digest(*, subject: str, revision: str) -> str:
    """Цифра ПЕРЕХОДА: что именно раскатывается и на что.

    Вердикт привязан к ней, поэтому улика о другой версии того же скилла — и тем
    более о другом скилле — не открывает этот переход.
    """
    if not isinstance(subject, str) or not subject:
        raise ActivationError("subject is required")
    if not isinstance(revision, str) or not revision:
        raise ActivationError("revision is required")
    return _digest({"subject": subject, "revision": revision})


@dataclass(frozen=True, slots=True)
class BroadActivationGrant:
    """Разрешение на ОДНО применение к парку.

    Подписано ключом улик: собрать его без ключа нельзя, поэтому «обойти дверь»
    нельзя даже вызвав `consume` напрямую.
    """

    run_id: str
    revision_digest: str
    cohort_digest: str
    process_identity: str
    granted_at: float
    reason: str
    token: str


@dataclass(frozen=True, slots=True)
class ActivationDecision:
    """Что решила дверь. `grant is None` всегда означает «нельзя»."""

    run_id: str
    allowed: bool
    reason: str
    verdict: CanaryVerdict | None = None
    grant: BroadActivationGrant | None = None


class BroadActivationGate:
    """Долговечный канареечный прогон плюс единственная дверь к парку.

    Экземпляр — это СЛУЖБА, а не операционная система процесса:
    `process_identity` должен быть устойчивым именем деплоя, иначе перезапуск
    сам себе перестанет доверять. Личность процесса попадает в план, в улику и
    в строку прогона, поэтому ЧУЖАЯ служба чужой прогон не дорешивает.
    """

    __slots__ = ("_store", "_ledger", "_key", "process_identity", "owner_id")

    def __init__(self, store: ObjectiveStore, *, evidence_key: bytes,
                 process_identity: str, owner_id: str) -> None:
        if not isinstance(store, ObjectiveStore):
            raise ActivationError("a durable ObjectiveStore is required")
        if not isinstance(process_identity, str) or not process_identity:
            raise ActivationError("process_identity is required")
        if not isinstance(owner_id, str) or not owner_id:
            raise ActivationError("owner_id is required")
        self._ledger = CanaryEvidenceLedger(evidence_key)   # ключ проверяет ledger
        self._key = bytes(evidence_key)
        self._store = store
        self.process_identity = process_identity
        self.owner_id = owner_id

    # -------------------------------------------------------------- план

    def plan(self, *, subject: str, revision: str, members: Sequence[str],
             started_at: float, policy: CanaryPolicy | None = None,
             run_nonce: str = "") -> CanaryPlan:
        """Детерминированный план. Ничего не пишет и ничего не активирует.

        `started_at` и `run_nonce` обязаны быть ВОСПРОИЗВОДИМЫМИ фактами (время
        заведения сравнения, его идентификатор), а не «сейчас»: иначе каждый
        пересчёт заводил бы новый прогон, и долговечные отчёты предыдущего
        оказывались бы уликой «прошлого прогона» для самих себя.
        """
        return plan_canary(list(members), revision_digest=activation_digest(
            subject=subject, revision=revision), now=float(started_at), policy=policy,
            process_identity=self.process_identity, run_nonce=run_nonce)

    def open(self, plan: CanaryPlan, *, candidate: str) -> dict[str, Any]:
        """Записать прогон ДО того, как тронут парк. Повтор — не новая попытка.

        Идемпотентно по `run_id`: план детерминирован, поэтому второй вызов на
        тех же фактах находит ту же строку, а не заводит вторую.
        """
        try:
            return self._store.open_canary_run(
                run_id=plan.run_id, revision_digest=plan.revision_digest,
                cohort_digest=plan.cohort_digest, population=plan.population,
                cohort=plan.cohort, candidates={m: str(candidate) for m in plan.population},
                started_at=plan.started_at, process_identity=self.process_identity,
                owner_id=self.owner_id)
        except ObjectiveStoreError:
            run = self._store.canary_run(plan.run_id)
            if (run["revision_digest"] != plan.revision_digest
                    or run["cohort_digest"] != plan.cohort_digest
                    or tuple(sorted(run["cohort"])) != plan.cohort
                    or run["process_identity"] != self.process_identity):
                raise ActivationError(REASON_TAMPERED) from None
            return run

    def plan_of(self, run_id: str) -> CanaryPlan:
        """Восстановить план из ДОЛГОВЕЧНОЙ строки прогона.

        Пересчитанная цифра когорты обязана совпасть с записанной: строка,
        которую правили в базе, — это отказ, а не план.
        """
        run = self._store.canary_run(run_id)
        cohort = tuple(sorted(run["cohort"]))
        if _digest({"r": run["revision_digest"], "c": list(cohort)}) != run["cohort_digest"]:
            raise ActivationError(REASON_TAMPERED)
        return CanaryPlan(revision_digest=run["revision_digest"], cohort=cohort,
                          population=tuple(sorted(run["population"])),
                          started_at=float(run["started_at"]),
                          process_identity=run["process_identity"],
                          cohort_digest=run["cohort_digest"], run_id=run["run_id"])

    def state(self, run_id: str) -> str:
        return str(self._store.canary_run(run_id)["state"])

    # ------------------------------------------------------------ отчёты

    def issue_evidence(self, run_id: str, member: str, *, at: float) -> str:
        return self._ledger.issue(self.plan_of(run_id), member, issued_at=at)

    def report_healthy(self, run_id: str, member: str, *, at: float, detail: str = "") -> str:
        """Заявка о здоровье ВСЕГДА едет с привязанной уликой."""
        evidence_ref = self.issue_evidence(run_id, member, at=at)
        self._store.record_canary_report(run_id, member, healthy=True,
                                         evidence_ref=evidence_ref, detail=detail, at=at)
        return evidence_ref

    def report_unhealthy(self, run_id: str, member: str, *, at: float,
                         detail: str = "") -> None:
        """Падение улики не требует: иначе испорченная улика отменяла бы падение."""
        self._store.record_canary_report(run_id, member, healthy=False, evidence_ref="",
                                         detail=detail, at=at)

    def report_raw(self, run_id: str, member: str, *, healthy: bool | None,
                   evidence_ref: str = "", detail: str = "", at: float) -> None:
        """Отчёт, пришедший СНАРУЖИ: улику не выпускаем, а проверяем на двери."""
        self._store.record_canary_report(run_id, member, healthy=healthy,
                                         evidence_ref=evidence_ref, detail=detail, at=at)

    def reported_members(self, run_id: str) -> set[str]:
        return {row["objective_id"] for row in self._store.canary_reports(run_id)}

    def outcomes(self, run_id: str) -> tuple[CanaryOutcome, ...]:
        return tuple(CanaryOutcome(objective_id=row["objective_id"], healthy=row["healthy"],
                                   evidence_ref=row["evidence_ref"], detail=row["detail"])
                     for row in self._store.canary_reports(run_id))

    # ------------------------------------------------------------- дверь

    def authorize(self, run_id: str, *, now: float) -> ActivationDecision:
        """ЕДИНСТВЕННАЯ дверь. Всё, что не «да», — «нет»."""
        try:
            run = self._store.canary_run(run_id)
        except ObjectiveStoreError:
            return ActivationDecision(run_id, False, REASON_UNKNOWN_RUN)
        if run["state"] != OPEN:
            return ActivationDecision(run_id, False, REASON_NOT_OPEN)
        if run["owner_id"] != self.owner_id:
            return ActivationDecision(run_id, False, REASON_WRONG_OWNER)
        if run["process_identity"] != self.process_identity:
            return ActivationDecision(run_id, False, REASON_WRONG_PROCESS)
        try:
            plan = self.plan_of(run_id)
        except ActivationError as exc:
            return ActivationDecision(run_id, False, str(exc))
        try:
            allowed, reason, verdict = authorize_broad_activation(
                plan, self.outcomes(run_id), resolve=self._ledger.resolve, now=float(now))
        except CanaryError as exc:
            return ActivationDecision(run_id, False, f"canary_refused:{exc}")
        if not allowed:
            return ActivationDecision(run_id, False, reason, verdict)
        return ActivationDecision(run_id, True, reason, verdict, self._mint(plan, reason, now))

    def consume(self, run_id: str, grant: Any, *, now: float, detail: str = "") -> CanaryPlan:
        """Израсходовать разрешение и закрыть прогон РОВНО ОДИН раз.

        Публична намеренно: обход двери должен ОТКАЗЫВАТЬ, а не быть невидимым.
        Вызывающий обязан звать это ПЕРЕД тем, как тронуть парк: закрытая строка
        без переключения — это несостоявшаяся активация, а переключение без
        закрытой строки — активация, о которой никто не узнает.
        """
        self._check_grant(run_id, grant, now=now)
        plan = self.plan_of(run_id)
        try:
            self._store.close_canary_run(run_id, state=ACTIVATED, decided_at=float(now),
                                         detail=detail[:400])
        except Exception as exc:                       # guarded UPDATE не сработал
            raise ActivationError(REASON_NOT_OPEN) from exc
        return plan

    def abandon(self, run_id: str, *, now: float, reason: str = "canary_failed") -> None:
        """Закрыть прогон без активации: откат/отказ — тоже долговечный факт."""
        run = self._store.canary_run(run_id)
        if run["state"] != OPEN:
            raise ActivationError(REASON_NOT_OPEN)
        self._store.close_canary_run(run_id, state=ABANDONED, decided_at=float(now),
                                     detail=str(reason)[:400])

    # -------------------------------------------------------- внутреннее

    def _grant_body(self, run_id: str, revision: str, cohort: str, process: str,
                    granted_at: float, reason: str) -> bytes:
        return json.dumps({"run_id": run_id, "revision_digest": revision,
                           "cohort_digest": cohort, "process_identity": process,
                           "granted_at": float(granted_at), "reason": reason},
                          sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")

    def _mint(self, plan: CanaryPlan, reason: str, now: float) -> BroadActivationGrant:
        body = self._grant_body(plan.run_id, plan.revision_digest, plan.cohort_digest,
                                self.process_identity, now, reason)
        return BroadActivationGrant(
            run_id=plan.run_id, revision_digest=plan.revision_digest,
            cohort_digest=plan.cohort_digest, process_identity=self.process_identity,
            granted_at=float(now), reason=reason,
            token=hmac.new(self._key, body, hashlib.sha256).hexdigest())

    def _check_grant(self, run_id: str, grant: Any, *, now: float) -> None:
        if grant is None:
            raise ActivationError(REASON_NO_GRANT)
        if not isinstance(grant, BroadActivationGrant):
            raise ActivationError(REASON_BAD_GRANT)
        if grant.run_id != run_id or grant.process_identity != self.process_identity:
            raise ActivationError(REASON_BAD_GRANT)
        body = self._grant_body(grant.run_id, grant.revision_digest, grant.cohort_digest,
                                grant.process_identity, grant.granted_at, grant.reason)
        expected = hmac.new(self._key, body, hashlib.sha256).hexdigest()
        if not isinstance(grant.token, str) or not hmac.compare_digest(expected, grant.token):
            raise ActivationError(REASON_BAD_GRANT)
        if not (grant.granted_at <= float(now) <= grant.granted_at + GRANT_TTL_SECONDS):
            raise ActivationError(REASON_STALE_GRANT)
        try:
            run = self._store.canary_run(run_id)
        except ObjectiveStoreError as exc:
            raise ActivationError(REASON_UNKNOWN_RUN) from exc
        if (grant.revision_digest != run["revision_digest"]
                or grant.cohort_digest != run["cohort_digest"]
                or grant.process_identity != run["process_identity"]):
            raise ActivationError(REASON_BAD_GRANT)


def cohort_reports_from_facts(gate: BroadActivationGate, run_id: str,
                              facts: Mapping[str, bool | None], *, at: float) -> None:
    """Записать отчёты когорты из ДОЛГОВЕЧНЫХ фактов, по одному разу на члена.

    Здоровье не заявляется прозой: `facts[member]` берётся из записи об исходе
    (у Command Center — терминальный статус `task_runs`). `None` — «исхода ещё
    нет»: это МОЛЧАНИЕ, а не успех, и дверь на нём закрыта.
    """
    plan = gate.plan_of(run_id)
    already = gate.reported_members(run_id)
    for member in plan.cohort:
        if member in already:
            continue
        healthy = facts.get(member)
        if healthy is None:
            continue                                  # молчание пишется отсутствием отчёта
        if healthy:
            gate.report_healthy(run_id, member, at=at)
        else:
            gate.report_unhealthy(run_id, member, at=at, detail="terminal outcome: failed")


__all__ = ["ABANDONED", "ACTIVATED", "OPEN", "ActivationDecision", "ActivationError",
           "BroadActivationGate", "BroadActivationGrant", "GRANT_TTL_SECONDS",
           "REASON_BAD_GRANT", "REASON_NOT_OPEN", "REASON_NO_GRANT", "REASON_STALE_GRANT",
           "REASON_TAMPERED", "REASON_UNKNOWN_RUN", "REASON_WRONG_OWNER",
           "REASON_WRONG_PROCESS", "activation_digest", "cohort_reports_from_facts"]
