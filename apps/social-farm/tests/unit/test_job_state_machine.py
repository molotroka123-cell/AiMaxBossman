"""Автомат работы и запрет слепого повтора.

Публикация, ушедшая дважды, не чинится откатом. Поэтому здесь проверяется не
столько корректность переходов, сколько то, что из состояний с возможным
внешним эффектом нет дороги в повтор.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from social_farm.domain.jobs import (EXTERNAL_EFFECT_POSSIBLE, TERMINAL, ExternalState,
                                     JobState, TransitionError, UnsafeRetry,
                                     allowed_transitions, can_transition,
                                     check_transition, guard_retry, on_restart,
                                     on_uncertain_response, reconciliation_outcome)

SPEC = Path(__file__).resolve().parents[3].parent / "_staging" / "social_farm"

# Автомат из спецификации, дословно. Наши два добавленных ребра здесь НЕ
# перечислены — тест ниже проверяет, что мы добавили ровно их и ничего больше.
SPEC_STATES = {
    "DRAFT": ["QUEUED", "CANCELLED"],
    "QUEUED": ["WAITING_APPROVAL", "READY", "CANCELLED"],
    "WAITING_APPROVAL": ["READY", "CANCELLED"],
    "READY": ["RUNNING", "CANCELLED"],
    "RUNNING": ["WAITING_PROVIDER", "RETRY_WAIT", "RECONCILING", "SUCCEEDED", "FAILED"],
    "WAITING_PROVIDER": ["RECONCILING", "SUCCEEDED", "FAILED", "RETRY_WAIT"],
    "RETRY_WAIT": ["READY", "FAILED", "DEAD_LETTER", "CANCELLED"],
    "RECONCILING": ["SUCCEEDED", "FAILED", "RETRY_WAIT", "DEAD_LETTER"],
    "SUCCEEDED": [], "FAILED": ["READY"], "CANCELLED": [], "DEAD_LETTER": [],
}
OUR_ADDITIONS = {("RUNNING", "WAITING_APPROVAL"), ("RUNNING", "CANCELLED")}


def test_every_transition_of_the_spec_is_implemented():
    for source, targets in SPEC_STATES.items():
        for target in targets:
            assert can_transition(JobState(source), JobState(target)), \
                f"потерян переход из спецификации: {source} → {target}"


def test_we_added_exactly_two_edges_and_documented_why():
    """Отклонение от спецификации допустимо, но обязано быть ровно тем,
    которое объявлено. Молча добавленный переход — это молча изменённое
    поведение."""
    extra = set()
    for state in JobState:
        for target in allowed_transitions(state):
            if target.value not in SPEC_STATES[state.value]:
                extra.add((state.value, target.value))
    assert extra == OUR_ADDITIONS, f"незадокументированные переходы: {extra - OUR_ADDITIONS}"


def test_terminal_states_have_no_way_out():
    for state in (JobState.SUCCEEDED, JobState.CANCELLED, JobState.DEAD_LETTER):
        assert allowed_transitions(state) == frozenset()
        assert state in TERMINAL


def test_forbidden_transition_names_what_is_allowed():
    with pytest.raises(TransitionError, match="не разрешён"):
        check_transition(JobState.SUCCEEDED, JobState.READY)
    with pytest.raises(TransitionError, match="DRAFT"):
        check_transition(JobState.DRAFT, JobState.RUNNING)


def test_stale_running_goes_to_reconciling_not_to_ready():
    """Процесс мог умереть сразу после того, как провайдер принял запрос.

    `READY` здесь означал бы вторую публикацию, `FAILED` — потерянную первую.
    Единственный честный ответ — пойти и выяснить.
    """
    assert on_restart(JobState.RUNNING) is JobState.RECONCILING
    # остальные состояния перезапуск не трогает
    for state in (JobState.QUEUED, JobState.READY, JobState.WAITING_APPROVAL):
        assert on_restart(state) is state


def test_uncertain_provider_response_goes_to_reconciling():
    assert on_uncertain_response(JobState.RUNNING) is JobState.RECONCILING
    assert on_uncertain_response(JobState.WAITING_PROVIDER) is JobState.RECONCILING


# ------------------------------------------------------------------ запрет повтора

def test_retry_is_refused_while_the_external_effect_is_unknown():
    for state in EXTERNAL_EFFECT_POSSIBLE:
        with pytest.raises(UnsafeRetry, match="Сначала сверка"):
            guard_retry(state, ExternalState.UNKNOWN)


def test_retry_is_refused_even_from_a_safe_state_when_state_is_unknown():
    with pytest.raises(UnsafeRetry):
        guard_retry(JobState.RETRY_WAIT, ExternalState.UNKNOWN)


def test_retry_is_allowed_only_when_absence_was_proven():
    guard_retry(JobState.RETRY_WAIT, ExternalState.ABSENT)      # не бросает
    guard_retry(JobState.READY, ExternalState.NONE)


def test_confirmed_effect_is_never_retried():
    with pytest.raises(UnsafeRetry):
        guard_retry(JobState.RECONCILING, ExternalState.CONFIRMED)


def test_reconciliation_that_proves_nothing_stops_the_job():
    """Спека не говорит, что делать, когда провайдер не даёт способа проверить.

    Выбор: остановиться и ждать человека. Неопределённость лучше разрешать
    глазами, чем вторым сообщением наружу.
    """
    assert reconciliation_outcome(effect_found=True) == (JobState.SUCCEEDED,
                                                         ExternalState.CONFIRMED)
    assert reconciliation_outcome(effect_found=False) == (JobState.RETRY_WAIT,
                                                          ExternalState.ABSENT)
    assert reconciliation_outcome(effect_found=None) == (JobState.DEAD_LETTER,
                                                         ExternalState.UNKNOWN)


def test_reconciliation_result_is_a_legal_transition():
    """Исход сверки должен быть достижим из RECONCILING, иначе он бесполезен."""
    for found in (True, False, None):
        target, _ = reconciliation_outcome(effect_found=found)
        check_transition(JobState.RECONCILING, target)


@pytest.mark.skipif(not (SPEC / "schemas" / "job_state_machine.yaml").exists(),
                    reason="пакет спецификации не распакован рядом")
def test_our_copy_of_the_spec_machine_matches_the_file():
    """Если файл спецификации поменяется, тест выше перестанет что-то значить."""
    text = (SPEC / "schemas" / "job_state_machine.yaml").read_text(encoding="utf-8")
    for state, targets in SPEC_STATES.items():
        line = next((ln for ln in text.splitlines()
                     if ln.strip().startswith(f"{state}:")), None)
        assert line is not None, f"состояние {state} исчезло из спецификации"
        listed = [t.strip() for t in line.split(":", 1)[1].strip(" []").split(",")
                  if t.strip()]
        assert listed == targets, f"{state}: спека {listed}, наша копия {targets}"
