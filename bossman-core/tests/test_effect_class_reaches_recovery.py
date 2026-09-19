"""Объявленный класс эффекта должен ДОХОДИТЬ до восстановления после срыва.

Сценарий владельца №18 («убит посреди задачи — возобновляется») нашёл это
прогоном, а не чтением: после убийства С РАБОТОЙ В ПОЛЁТЕ задача не
возобновляется. Бронь паркуется с `irreversible_effect_not_confirmed`, и
следующий допуск отбивается `admission_state_changed`.

Причина: `objective_recovery._effect_class()` ищет `payload['effect_class']`, а
`AdmissionProposal.to_payload()` такого ключа НЕ КЛАДЁТ вовсе. Значение не
входит в `EFFECT_CLASSES`, и функция честно возвращает `IRREVERSIBLE`.

ЭТО НЕ НЕВЕРНОЕ ПОВЕДЕНИЕ, а ПРОБЕЛ, и разница здесь принципиальна. Парковка
при неизвестном классе — безопасная сторона: незавершённый необратимый эффект
нельзя молча повторить. Дефект в том, что объявить идемпотентность БЫЛО НЕЧЕМ.

Поэтому правка даёт способ объявить класс и НИЧЕГО не ослабляет по умолчанию:
необъявленное по-прежнему паркуется.
"""
from __future__ import annotations

import pytest

from bossman_shared import objective_admission as adm
from bossman_shared import objective_recovery as rec


def payload_with(effects):
    proposal = adm.AdmissionProposal(
        proposal_id="p1", objective_id="o1", objective_digest="d", objective_revision=1,
        created_at=1000.0, valid_until=2000.0, freshness_deadline=2000.0,
        observation_digests=(), trigger="test", expected_effects=tuple(effects))
    return proposal.to_payload()


# --- то, что НЕ должно измениться -------------------------------------------

def test_an_undeclared_effect_is_still_irreversible():
    """Безопасная сторона сохранена: не объявил — значит необратимо."""
    assert rec._effect_class(payload_with([])) == rec.IRREVERSIBLE


def test_a_garbage_class_is_still_irreversible():
    assert rec._effect_class(payload_with([{"effect_class": "МОЖНО-ПОВТОРЯТЬ"}])) == rec.IRREVERSIBLE


def test_one_irreversible_among_idempotent_makes_the_whole_reservation_irreversible():
    """Самый строгий эффект решает за всю бронь.

    Иначе идемпотентную запись можно было бы приложить к необратимой отправке
    денег и получить разрешение повторить обе.
    """
    mixed = [{"effect_class": rec.IDEMPOTENT}, {"effect_class": rec.IRREVERSIBLE}]
    assert rec._effect_class(payload_with(mixed)) == rec.IRREVERSIBLE


# --- то, ради чего правка ----------------------------------------------------

def test_a_declared_idempotent_effect_reaches_recovery():
    """Объявленная идемпотентность доходит до восстановления."""
    assert rec._effect_class(payload_with([{"effect_class": rec.IDEMPOTENT}])) == rec.IDEMPOTENT


def test_all_retryable_effects_make_the_reservation_retryable():
    both = [{"effect_class": rec.IDEMPOTENT}, {"effect_class": rec.REVERSIBLE}]
    assert rec._effect_class(payload_with(both)) in rec.RETRYABLE_CLASSES


@pytest.mark.parametrize("declared,expected_disposition", [
    (rec.IDEMPOTENT, rec.RELEASED),
    (rec.REVERSIBLE, rec.RELEASED),
    (rec.IRREVERSIBLE, rec.PARKED),
])
def test_the_decision_follows_the_declared_class(declared, expected_disposition):
    """Сквозь настоящую _decide, а не только через классификатор.

    Пара: retryable-классы отпускаются на НОВЫЙ допуск, необратимый паркуется и
    требует владельца.
    """
    effect_class = rec._effect_class(payload_with([{"effect_class": declared}]))
    disposition, reason, requires_owner = rec._decide(effect_class, rec.NOT_APPLIED)
    assert disposition == expected_disposition
    assert requires_owner is (expected_disposition == rec.PARKED)


def test_an_applied_effect_commits_whatever_its_class():
    """Контроль: подтверждённый эффект фиксируется и у необратимого."""
    for declared in (rec.IDEMPOTENT, rec.IRREVERSIBLE):
        cls = rec._effect_class(payload_with([{"effect_class": declared}]))
        disposition, _, requires_owner = rec._decide(cls, rec.APPLIED)
        assert disposition == rec.COMMITTED and requires_owner is False
