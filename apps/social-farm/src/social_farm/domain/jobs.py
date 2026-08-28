"""Автомат состояний работы и правила, по которым он переходит.

Автомат взят из `schemas/job_state_machine.yaml` дословно, с двумя
добавленными рёбрами — спека их не содержит, но требует поведения, которое без
них невозможно (`DIGEST_CORE` C10):

* `RUNNING → WAITING_APPROVAL` — политика оценивается ПОВТОРНО в момент
  исполнения (`57_CONTENT_PIPELINE_DETAILED` §11). Если она успела стать `ASK`,
  идти некуда, а выполнить нельзя.
* `RUNNING → CANCELLED` — отмена запущенной работы, которую требует
  `POST /jobs/{id}/cancel`, и терминал для `DENY` при повторной оценке.

Оба добавления помечены в коде и записаны в `PRE_IMPLEMENTATION_AUDIT.md` §4.

Главное правило всего файла: **работа с возможно случившимся внешним эффектом
никогда не уходит в повтор**. Она уходит в сверку. Публикация, отправленная
дважды, не чинится откатом.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class JobState(str, Enum):
    DRAFT = "DRAFT"
    QUEUED = "QUEUED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_PROVIDER = "WAITING_PROVIDER"
    RETRY_WAIT = "RETRY_WAIT"
    RECONCILING = "RECONCILING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    DEAD_LETTER = "DEAD_LETTER"


class ExternalState(str, Enum):
    """Что мы знаем о внешнем эффекте. `UNKNOWN` — не «нет», а «неизвестно»."""

    NONE = "NONE"
    UNKNOWN = "UNKNOWN"
    CONFIRMED = "CONFIRMED"
    ABSENT = "ABSENT"


_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.DRAFT: frozenset({JobState.QUEUED, JobState.CANCELLED}),
    JobState.QUEUED: frozenset({JobState.WAITING_APPROVAL, JobState.READY,
                                JobState.CANCELLED}),
    JobState.WAITING_APPROVAL: frozenset({JobState.READY, JobState.CANCELLED}),
    JobState.READY: frozenset({JobState.RUNNING, JobState.CANCELLED}),
    JobState.RUNNING: frozenset({JobState.WAITING_PROVIDER, JobState.RETRY_WAIT,
                                 JobState.RECONCILING, JobState.SUCCEEDED,
                                 JobState.FAILED,
                                 # добавлено нами, см. докстроку модуля
                                 JobState.WAITING_APPROVAL, JobState.CANCELLED}),
    JobState.WAITING_PROVIDER: frozenset({JobState.RECONCILING, JobState.SUCCEEDED,
                                          JobState.FAILED, JobState.RETRY_WAIT}),
    JobState.RETRY_WAIT: frozenset({JobState.READY, JobState.FAILED,
                                    JobState.DEAD_LETTER, JobState.CANCELLED}),
    JobState.RECONCILING: frozenset({JobState.SUCCEEDED, JobState.FAILED,
                                     JobState.RETRY_WAIT, JobState.DEAD_LETTER}),
    JobState.SUCCEEDED: frozenset(),
    JobState.FAILED: frozenset({JobState.READY}),
    JobState.CANCELLED: frozenset(),
    JobState.DEAD_LETTER: frozenset(),
}

TERMINAL = frozenset({JobState.SUCCEEDED, JobState.CANCELLED, JobState.DEAD_LETTER})

# Состояния, в которых внешний эффект уже мог произойти. Из них нельзя
# повторять действие, не выяснив, что случилось на той стороне.
EXTERNAL_EFFECT_POSSIBLE = frozenset({JobState.RUNNING, JobState.WAITING_PROVIDER,
                                      JobState.RECONCILING})


class TransitionError(ValueError):
    """Переход, которого нет в автомате."""


class UnsafeRetry(RuntimeError):
    """Попытка повторить действие с неизвестным внешним состоянием."""


def allowed_transitions(state: JobState) -> frozenset[JobState]:
    return _TRANSITIONS[state]


def can_transition(source: JobState, target: JobState) -> bool:
    return target in _TRANSITIONS[source]


def check_transition(source: JobState, target: JobState) -> None:
    if not can_transition(source, target):
        allowed = ", ".join(sorted(s.value for s in _TRANSITIONS[source])) or "нет"
        raise TransitionError(
            f"переход {source.value} → {target.value} не разрешён; "
            f"из {source.value} допустимы: {allowed}")


def on_restart(state: JobState) -> JobState:
    """Что делать с работой, застрявшей в `RUNNING` после перезапуска.

    `restart_rule: stale_RUNNING: RECONCILING`. Не `READY` и не `FAILED`:
    процесс мог умереть сразу после того, как провайдер принял запрос, и
    единственный честный ответ — пойти и выяснить.
    """
    if state is JobState.RUNNING:
        return JobState.RECONCILING
    return state


def on_uncertain_response(state: JobState) -> JobState:
    """Ответ провайдера не даёт понять, случился ли эффект.

    `external_effect_rule: uncertain_response: RECONCILING`.
    """
    check_transition(state, JobState.RECONCILING)
    return JobState.RECONCILING


def guard_retry(state: JobState, external_state: ExternalState) -> None:
    """Разрешить повтор можно, только если доказано отсутствие внешнего эффекта.

    `54_JOB_LEASES_CHECKPOINTS`: «Only if reconciliation proves no external
    effect may retry». Доказательство — это `ABSENT`, а не отсутствие
    доказательства обратного.
    """
    if external_state is ExternalState.ABSENT or external_state is ExternalState.NONE:
        return
    if state in EXTERNAL_EFFECT_POSSIBLE or external_state is ExternalState.UNKNOWN:
        raise UnsafeRetry(
            f"повтор запрещён: внешнее состояние {external_state.value} в состоянии "
            f"{state.value}. Сначала сверка — вслепую повторять действие, которое "
            f"могло дойти до провайдера, нельзя.")


def reconciliation_outcome(*, effect_found: bool | None) -> tuple[JobState, ExternalState]:
    """Исход сверки. `None` означает «выяснить не удалось».

    Спека не говорит, что делать в этом случае (`DIGEST_CORE` G5). Решение:
    `DEAD_LETTER`, а не повтор. Работа останавливается и ждёт человека —
    неопределённость лучше разрешать глазами, чем вторым сообщением наружу.
    """
    if effect_found is True:
        return JobState.SUCCEEDED, ExternalState.CONFIRMED
    if effect_found is False:
        return JobState.RETRY_WAIT, ExternalState.ABSENT
    return JobState.DEAD_LETTER, ExternalState.UNKNOWN


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """Отметка о пройденном этапе. Позволяет продолжить, а не начать заново."""

    name: str
    at: str

    # Порядок этапов работы публикации. `MEDIA_RENDERED` здесь означает
    # «производные ассеты ревизии на месте и валидны» — сам рендер происходит
    # ДО ревизии (решение C9), иначе approval не к чему привязать.
    ORDER = ("POLICY_EVALUATED", "APPROVAL_GRANTED", "MEDIA_RENDERED",
             "PROVIDER_CONTAINER_CREATED", "PROVIDER_PUBLISHED", "RECONCILED")


__all__ = ["Checkpoint", "EXTERNAL_EFFECT_POSSIBLE", "ExternalState", "JobState",
           "TERMINAL", "TransitionError", "UnsafeRetry", "allowed_transitions",
           "can_transition", "check_transition", "guard_retry", "on_restart",
           "on_uncertain_response", "reconciliation_outcome"]
